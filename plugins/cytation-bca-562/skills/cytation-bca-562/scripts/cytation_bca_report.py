#!/usr/bin/env python3
"""Create a portrait A4 PDF report from a BioTek Cytation 562-nm BCA XLSX export.

The script is deliberately self-contained so the skill can be invoked on an
incoming workbook without modifying the source file.  It validates the
machine-generated Cytation metadata, finds the labelled 8 x 12 plate block,
fits an equal-weight 4PL to the eight A/B duplicate means, and renders the
three requested report sections.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import warnings
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable, Optional
from PIL import Image

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import cm
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.font_manager import FontProperties
from openpyxl import load_workbook
from scipy.optimize import least_squares
from scipy.special import expit

A4 = (8.27, 11.69)
ROWS = tuple("ABCDEFGH")
COLS = tuple(range(1, 13))
DEFAULT_STANDARD_CONC_UG_ML = np.array([2000.0, 1500.0, 1000.0, 750.0, 500.0, 250.0, 125.0, 0.0])
DEFAULT_STANDARD_CONC_UG_UL = DEFAULT_STANDARD_CONC_UG_ML / 1000.0
# Use the plain warning-sign code point without an emoji variation selector;
# this renders once in the PDF and remains searchable in extracted text.
WARNING_MARK = "⚠"
EMPTY_WELL_CUTOFF_OD = 0.065
OVERFLOW_OD = 4.0


def parse_concentrations_arg(conc_str: Optional[str]) -> tuple[np.ndarray, np.ndarray]:
    """Parse standard nominal concentrations in ug/mL. Returns (ug_ml, ug_ul)."""
    if not conc_str:
        return DEFAULT_STANDARD_CONC_UG_ML.copy(), DEFAULT_STANDARD_CONC_UG_UL.copy()
    tokens = [float(t.strip()) for t in conc_str.split(",") if t.strip()]
    if len(tokens) != 8:
        raise ValueError(f"standard concentrations must contain exactly 8 values, got {len(tokens)}")
    ug_ml = np.array(tokens, dtype=float)
    return ug_ml, ug_ml / 1000.0


def parse_well_coordinate(coord_str: str) -> tuple[int, int]:
    """Parse a single well coordinate like 'A1' or 'h12' into (row_idx, col_idx)."""
    m = re.match(r"^([A-Ha-h])([1-9]|1[0-2])$", coord_str.strip())
    if not m:
        raise ValueError(f"invalid well coordinate: {coord_str!r}")
    row_idx = ROWS.index(m.group(1).upper())
    col_idx = int(m.group(2)) - 1
    return row_idx, col_idx


def parse_well_range(range_str: str) -> list[tuple[int, int]]:
    """Parse a well range (e.g. 'A1:B8', 'A1', 'C1:D12') into a list of (row_idx, col_idx)."""
    tokens = [t.strip() for t in range_str.split(",") if t.strip()]
    wells: list[tuple[int, int]] = []
    for token in tokens:
        if ":" in token:
            start_str, end_str = token.split(":", 1)
            r1, c1 = parse_well_coordinate(start_str)
            r2, c2 = parse_well_coordinate(end_str)
            row_min, row_max = min(r1, r2), max(r1, r2)
            col_min, col_max = min(c1, c2), max(c1, c2)
            for r in range(row_min, row_max + 1):
                for c in range(col_min, col_max + 1):
                    wells.append((r, c))
        else:
            wells.append(parse_well_coordinate(token))
    return wells


def parse_standards_arg(standards_str: Optional[str]) -> list[tuple[str, str]]:
    """Parse standards argument into 8 pairs of well coordinate names.
    
    Default is A1:B8 -> [(A1, B1), (A2, B2), ..., (A8, B8)].
    If a range of 2 rows x 8 cols or 8 rows x 2 cols is given (e.g. C1:D8),
    it generates the 8 duplicate pairs in order 2.0 down to 0.0 ug/uL.
    """
    if not standards_str:
        return [(f"A{i+1}", f"B{i+1}") for i in range(8)]
    
    # Check if format like "C1:D8"
    if ":" in standards_str and "," not in standards_str:
        parts = standards_str.split(":", 1)
        r1, c1 = parse_well_coordinate(parts[0])
        r2, c2 = parse_well_coordinate(parts[1])
        row_min, row_max = min(r1, r2), max(r1, r2)
        col_min, col_max = min(c1, c2), max(c1, c2)
        num_rows = row_max - row_min + 1
        num_cols = col_max - col_min + 1
        if num_rows == 2 and num_cols == 8:
            # 2 rows x 8 cols: pairs are (Row1,Col_i), (Row2,Col_i)
            r_top, r_bot = row_min, row_max
            return [(f"{ROWS[r_top]}{col_min + i + 1}", f"{ROWS[r_bot]}{col_min + i + 1}") for i in range(8)]
        elif num_rows == 8 and num_cols == 2:
            # 8 rows x 2 cols: pairs are (Row_i,Col1), (Row_i,Col2)
            c_left, c_right = col_min, col_max
            return [(f"{ROWS[row_min + i]}{c_left + 1}", f"{ROWS[row_min + i]}{c_right + 1}") for i in range(8)]
        elif num_rows == 8 and num_cols == 12:
            raise ValueError(f"standards range {standards_str!r} defines full plate; specify 16 wells (e.g. A11:H12 or 11:12)")
        else:
            raise ValueError(f"standards range {standards_str!r} must define 16 wells (2x8 or 8x2) for 8 duplicate pairs")
    
    # Comma-separated list of 16 wells or 8 pairs
    items = [t.strip() for t in standards_str.split(",") if t.strip()]
    if len(items) == 8:
        # e.g. "A1+B1,A2+B2,..." or "A1:B1,A2:B2,..."
        pairs = []
        for it in items:
            p = re.split(r"[\+: ]+", it)
            if len(p) != 2:
                raise ValueError(f"cannot parse standard pair {it!r}")
            # validate
            parse_well_coordinate(p[0])
            parse_well_coordinate(p[1])
            pairs.append((p[0].upper(), p[1].upper()))
        return pairs
    elif len(items) == 16:
        # 16 sequential wells paired 2 by 2
        pairs = []
        for i in range(0, 16, 2):
            parse_well_coordinate(items[i])
            parse_well_coordinate(items[i+1])
            pairs.append((items[i].upper(), items[i+1].upper()))
        return pairs
    else:
        raise ValueError(f"standards argument must specify 8 pairs or a 16-well range, got {standards_str!r}")


def parse_exclude_arg(exclude_str: Optional[str]) -> set[tuple[int, int]]:
    """Parse comma-separated well coordinates/ranges to exclude."""
    if not exclude_str:
        return set()
    return set(parse_well_range(exclude_str))




class InputError(ValueError):
    """Raised when an input workbook cannot be unambiguously interpreted."""


@dataclass(frozen=True)
class PlateCandidate:
    sheet_name: str
    start_row: int
    start_col: int
    values: np.ndarray
    header_score: int
    local_562: bool


@dataclass(frozen=True)
class FitResult:
    bottom: float
    top: float
    ec50: float
    hill_slope: float
    predicted_means: np.ndarray
    r2: float
    rmse: float
    monotonic: bool
    physical_ascending: bool
    optimizer_success: bool
    optimizer_message: str


def fit_identifiability_warning(fit: FitResult, std_conc_ug_ul: np.ndarray) -> Optional[str]:
    """Describe the common BCA case where the upper plateau is not sampled.

    A free four-parameter fit can have an effectively unbounded top/EC50 when
    the highest standard is still below the upper asymptote.  This is not a
    parser failure and the fitted response curve remains usable over the
    observed range, but the report should not imply that the upper plateau is
    well estimated.
    """
    if not (math.isfinite(fit.top) and math.isfinite(fit.ec50)):
        return "upper asymptote and/or EC50 is not finite"
    if fit.top > 100.0 * max(1.0, abs(fit.bottom), abs(fit.predicted_means[0])) or fit.ec50 > 100.0 * float(np.max(std_conc_ug_ul)):
        return "upper asymptote is weakly identified because the standards do not reach a plateau"
    return None


@dataclass(frozen=True)
class InverseResult:
    value: Optional[float]
    reasons: tuple[str, ...]


def finite_number(value: Any) -> Optional[float]:
    """Return a finite scalar number, rejecting booleans and Excel errors."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        if text.upper() in ("OVRFLW", "OVERFLOW", ">4.0", "> 4.0", ">4"):
            return OVERFLOW_OD
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def display_number(value: Any) -> str:
    """Preserve the source-like decimal precision for raw numeric cells."""
    if isinstance(value, str) and value.strip().upper() in ("OVRFLW", "OVERFLOW", ">4.0", "> 4.0", ">4"):
        return value.strip()
    number = finite_number(value)
    if number is None:
        return "N/A"
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return str(int(value))
    # Python's shortest round-trip representation matches the General-format
    # lexical values used by Cytation exports for the example (0.41, 0.09,
    # 0.902), unlike a forced three-decimal format.
    return str(value).strip() if isinstance(value, str) else repr(float(number))


def display_concentration(result: InverseResult, show_warning: bool = True) -> str:
    if result.value is None:
        return f"N/A {WARNING_MARK}" if show_warning else "N/A"
    value = f"{result.value:.3f}"
    return f"{value} {WARNING_MARK}" if show_warning and result.reasons else value


def cell_text(cell: Any) -> str:
    value = cell.value
    return "" if value is None else str(value)


def worksheet_text(ws: Any) -> str:
    values: list[str] = []
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                values.append(cell_text(cell))
    return "\n".join(values)


def normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_wavelength_mentions(text: str) -> list[float]:
    """Extract wavelengths only from labelled wavelength metadata."""
    found: list[float] = []
    for match in re.finditer(
        r"(?:wavelengths?|lambda)\s*[:=]?\s*([^\n;]+)", text, flags=re.IGNORECASE
    ):
        for token in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", match.group(1)):
            number = finite_number(token)
            if number is not None:
                found.append(number)
    unique: list[float] = []
    for number in found:
        if not any(math.isclose(number, old, rel_tol=0.0, abs_tol=1e-9) for old in unique):
            unique.append(number)
    return unique


def metadata_value(ws: Any, label_pattern: str) -> Any:
    pattern = re.compile(label_pattern, flags=re.IGNORECASE)
    for row in ws.iter_rows():
        for cell in row:
            if pattern.search(cell_text(cell)):
                # Cytation exports place values to the right; below is a safe
                # fallback for variants that stack label/value vertically.
                for candidate in (
                    ws.cell(cell.row, cell.column + 1),
                    ws.cell(cell.row + 1, cell.column),
                ):
                    if candidate.value is not None and candidate.value != "":
                        return candidate.value
    return None


def format_metadata_value(value: Any, kind: str = "text") -> str:
    if value is None:
        return "not available"
    if isinstance(value, datetime):
        if kind == "date":
            return value.date().isoformat()
        if kind == "time":
            return value.time().strftime("%H:%M:%S")
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    number = finite_number(value)
    if kind == "temperature" and number is not None:
        return f"{number:g} °C"
    return normalise_text(str(value))


def surrounding_has_562(ws: Any, start_row: int, start_col: int) -> bool:
    # The example repeats 562 in the column immediately after the 12 wells.
    for row in range(max(1, start_row - 2), min(ws.max_row, start_row + 9) + 1):
        for col in range(max(1, start_col - 2), min(ws.max_column, start_col + 17) + 1):
            value = finite_number(ws.cell(row, col).value)
            if value is not None and math.isclose(value, 562.0, rel_tol=0.0, abs_tol=1e-9):
                return True
            text = cell_text(ws.cell(row, col))
            if re.search(r"wavelength|lambda", text, flags=re.IGNORECASE) and re.search(
                r"\b562(?:\.0+)?\b", text
            ):
                return True
    return False


def scan_plate_candidates(ws: Any) -> list[PlateCandidate]:
    """Find labelled 8 x 12 numeric plate blocks without fixed row numbers."""
    candidates: dict[tuple[int, int], PlateCandidate] = {}
    max_row = ws.max_row
    max_col = ws.max_column

    def labels_match(row: int, col: int) -> bool:
        return [cell_text(ws.cell(row + i, col)).strip().upper() for i in range(8)] == list(ROWS)

    def headers_match(row: int, col: int) -> bool:
        header_values = [finite_number(ws.cell(row, col + i).value) for i in range(12)]
        return all(value is not None and math.isclose(value, i + 1, abs_tol=1e-9) for i, value in enumerate(header_values))

    # range() has an exclusive upper bound: include the final possible
    # 8-row/12-column window (e.g. rows 25–32 in the Cytation example).
    for start_row in range(2, max_row - 7 + 1):
        for start_col in range(2, max_col - 11 + 1):
            if not labels_match(start_row, start_col - 1):
                continue
            block: list[list[float]] = []
            valid = True
            for r in range(8):
                row_values: list[float] = []
                for c in range(12):
                    value = finite_number(ws.cell(start_row + r, start_col + c).value)
                    if value is None:
                        valid = False
                        break
                    row_values.append(value)
                if not valid:
                    break
                block.append(row_values)
            if not valid:
                continue
            score = 1 if headers_match(start_row - 1, start_col) else 0
            key = (start_row, start_col)
            candidates[key] = PlateCandidate(
                sheet_name=ws.title,
                start_row=start_row,
                start_col=start_col,
                values=np.asarray(block, dtype=float),
                header_score=score,
                local_562=surrounding_has_562(ws, start_row, start_col),
            )
    return list(candidates.values())


def validate_metadata(text: str, wavelengths: list[float], sheet_name: str) -> None:
    lower = text.lower()
    if not re.search(r"cytation\s*5", lower):
        raise InputError(f"sheet {sheet_name!r} is not identified as a Cytation5/Cytation 5 export")
    if not re.search(r"96\s*[- ]?\s*well", lower):
        raise InputError(f"sheet {sheet_name!r} does not identify a 96-well plate")
    if not re.search(r"absorbance[^\n;]*endpoint|endpoint[^\n;]*absorbance", lower):
        raise InputError(f"sheet {sheet_name!r} does not identify an absorbance endpoint read")
    if not any(math.isclose(wavelength, 562.0, abs_tol=1e-9) for wavelength in wavelengths):
        raise InputError(f"sheet {sheet_name!r} has no labelled 562-nm wavelength")


def choose_plate(input_path: Path) -> tuple[Any, PlateCandidate, dict[str, str], list[float]]:
    try:
        workbook = load_workbook(input_path, data_only=True, read_only=False)
    except Exception as exc:  # openpyxl has several exception types for malformed files
        raise InputError(f"could not open workbook: {exc}") from exc

    valid: list[tuple[Any, PlateCandidate, dict[str, str], list[float]]] = []
    failures: list[str] = []
    for ws in workbook.worksheets:
        text = worksheet_text(ws)
        wavelengths = parse_wavelength_mentions(text)
        try:
            validate_metadata(text, wavelengths, ws.title)
        except InputError as exc:
            failures.append(str(exc))
            continue
        candidates = scan_plate_candidates(ws)
        if not candidates:
            failures.append(f"sheet {ws.title!r} has no labelled 8 x 12 numeric plate block")
            continue
        if len(candidates) > 1:
            local_562 = [candidate for candidate in candidates if candidate.local_562]
            if len(local_562) == 1:
                candidates = local_562
            elif len(wavelengths) == 1 and math.isclose(wavelengths[0], 562.0, abs_tol=1e-9):
                # A single wavelength and multiple duplicate detections can
                # occur when an export has repeated row labels. Prefer the
                # highest-scoring header-labelled candidate only if unique.
                best_score = max(candidate.header_score for candidate in candidates)
                best = [candidate for candidate in candidates if candidate.header_score == best_score]
                candidates = best
            if len(candidates) != 1:
                failures.append(
                    f"sheet {ws.title!r} contains {len(candidates)} ambiguous plate blocks"
                )
                continue
        candidate = candidates[0]
        metadata = {
            "date": format_metadata_value(metadata_value(ws, r"^date$"), "date"),
            "time": format_metadata_value(metadata_value(ws, r"^time$"), "time"),
            "reader": format_metadata_value(metadata_value(ws, r"reader\s*type")),
            "serial": format_metadata_value(metadata_value(ws, r"reader\s*serial")),
            "plate_type": format_metadata_value(metadata_value(ws, r"^plate\s*type$")),
            "temperature": format_metadata_value(
                metadata_value(ws, r"actual\s*temperature"), "temperature"
            ),
            "wavelength": "562 nm",
        }
        valid.append((ws, candidate, metadata, wavelengths))

    if len(valid) != 1:
        if len(valid) > 1:
            raise InputError(
                f"workbook contains {len(valid)} valid Cytation plate sheets; multi-plate workbooks are ambiguous"
            )
        detail = "; ".join(failures[-4:])
        raise InputError(f"no valid Cytation 562-nm plate found{': ' + detail if detail else ''}")
    return valid[0]


def four_pl_model(x: np.ndarray, bottom: float, top: float, ec50: float, hill_slope: float) -> np.ndarray:
    """Ascending 4PL convention, with the explicit x=0 limiting value."""
    x_array = np.asarray(x, dtype=float)
    result = np.full(x_array.shape, np.nan, dtype=float)
    zero = np.isclose(x_array, 0.0, rtol=0.0, atol=0.0)
    result[zero] = bottom
    positive = x_array > 0
    if np.any(positive):
        if not math.isfinite(ec50) or ec50 <= 0 or not math.isfinite(hill_slope):
            return result
        log_ratio = hill_slope * (math.log(ec50) - np.log(x_array[positive]))
        result[positive] = bottom + (top - bottom) * expit(-log_ratio)
    return result


def fit_4pl(replicate_values: np.ndarray, std_conc_ug_ul: np.ndarray) -> FitResult:
    # Standard concentrations repeated for duplicate pairs
    x_pairs = std_conc_ug_ul.copy()
    x = np.repeat(x_pairs, 2)
    y = replicate_values.ravel()
    if y.shape != (16,) or not np.all(np.isfinite(y)):
        raise InputError("the sixteen standard replicate values are not finite")
    response_range = float(np.ptp(y))
    if response_range == 0:
        raise InputError("the standard values are identical; 4PL fit is undefined")
    
    means = np.mean(replicate_values, axis=1)
    blank_val = float(means[-1])
    
    # Gen5-standard 4PL constraints:
    # Bottom (A): bounded near blank [0.0, max(0.15, blank_val * 1.5)]
    # Hill slope (B): positive ascending [0.2, 3.0]
    # EC50 (C): [0.1, 20.0] ug/uL
    # Top (D): bounded to realistic detector saturation range [1.2, 4.0] OD
    bounds_lower = [0.0, 0.2, 0.1, 1.2]
    bounds_upper = [max(0.15, blank_val * 1.5), 3.0, 20.0, 4.0]
    
    initial_pairs = [
        (blank_val, 2.5, 2.0, 1.0),
        (blank_val, 3.0, 5.0, 0.8),
        (float(np.min(y)), 2.0, 1.0, 1.0),
        (blank_val, 3.5, 10.0, 0.7),
    ]

    def unpack(theta: np.ndarray) -> tuple[float, float, float, float]:
        return float(theta[0]), float(theta[1]), float(theta[2]), float(theta[3])

    def residual(theta: np.ndarray) -> np.ndarray:
        bottom, top, ec50, hill = unpack(theta)
        predicted = four_pl_model(x, bottom, top, ec50, hill)
        if not np.all(np.isfinite(predicted)):
            return np.full_like(y, 1e6, dtype=float)
        return predicted - y

    best: Optional[tuple[Any, tuple[float, float, float, float]]] = None
    for bottom, top, ec50, hill in initial_pairs:
        theta0 = np.array([bottom, top, ec50, hill], dtype=float)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = least_squares(
                    residual,
                    theta0,
                    bounds=([bounds_lower[0], bounds_lower[3], bounds_lower[2], bounds_lower[1]],
                            [bounds_upper[0], bounds_upper[3], bounds_upper[2], bounds_upper[1]]),
                    max_nfev=100000,
                    x_scale="jac",
                    ftol=1e-13,
                    xtol=1e-13,
                    gtol=1e-13,
                )
            params = unpack(result.x)
            predicted = four_pl_model(x, *params)
            if not np.all(np.isfinite(predicted)) or not math.isfinite(float(result.cost)):
                continue
            if best is None or float(result.cost) < float(best[0].cost):
                best = (result, params)
        except (ArithmeticError, FloatingPointError, ValueError):
            continue
    if best is None:
        raise InputError("the 4PL numerical fit failed")

    result, params = best
    bottom, top, ec50, hill = params
    predicted_means = four_pl_model(x_pairs, bottom, top, ec50, hill)
    residuals_means = predicted_means - means
    sse = float(np.sum(residuals_means**2))
    centered = means - float(np.mean(means))
    sst = float(np.sum(centered**2))
    r2 = float(1.0 - sse / sst) if sst > 0 else float("nan")
    rmse = float(math.sqrt(sse / len(means)))
    
    ascending_means = means[::-1]
    tolerance = max(1e-12, response_range * 1e-10)
    monotonic = bool(np.all(np.diff(ascending_means) >= -tolerance))
    physical_ascending = bool(
        math.isfinite(bottom)
        and math.isfinite(top)
        and math.isfinite(ec50)
        and math.isfinite(hill)
        and bottom < top
        and hill > 0
        and ec50 > 0
    )
    return FitResult(
        bottom=bottom,
        top=top,
        ec50=ec50,
        hill_slope=hill,
        predicted_means=predicted_means,
        r2=r2,
        rmse=rmse,
        monotonic=monotonic,
        physical_ascending=physical_ascending,
        optimizer_success=bool(result.success),
        optimizer_message=str(result.message),
    )


def inverse_4pl(
    absorbance: float,
    fit: FitResult,
    calibrator_min: float,
    calibrator_max: float,
    suppress_warnings: bool = False,
) -> InverseResult:
    """Invert the fitted model, retaining finite extrapolation with a flag."""
    bottom, top, ec50, hill = fit.bottom, fit.top, fit.ec50, fit.hill_slope
    def no_warning(reason: str) -> InverseResult:
        return InverseResult(None, () if suppress_warnings else (reason,))

    if not all(math.isfinite(value) for value in (absorbance, bottom, top, ec50, hill)):
        return no_warning("impossible inverse")
    if ec50 <= 0 or hill == 0 or top == bottom:
        return no_warning("impossible inverse")
    # Tolerance is based on absorbance values, not the fitted top.  A free
    # fit can have a very large, weakly identified top; including it here
    # would make an ordinary absorbance difference look "exact".
    scale = max(1.0, abs(bottom), abs(absorbance))
    tolerance = 1e-10 * scale
    if abs(absorbance - bottom) <= tolerance:
        value = 0.0
        reasons: list[str] = []
    else:
        q = (absorbance - bottom) / (top - bottom)
        if not math.isfinite(q):
            return no_warning("impossible inverse")
        if q == 1.0:
            # The upper asymptote is approached only as concentration tends
            # to infinity; there is no finite diagnostic to display.
            return no_warning("infinite inverse at fitted top")
        try:
            odds = q / (1.0 - q)
            if odds == 0.0 or not math.isfinite(odds):
                return no_warning("impossible inverse")
            # For q outside [0, 1], the ordinary inverse is not real for a
            # generally non-integer Hill slope.  Retain a useful signed
            # diagnostic by applying the same inverse-odds magnitude and
            # assigning the sign of the side of the fitted response range.
            diagnostic = q < 0.0 or q > 1.0
            log_value = math.log(ec50) + math.log(abs(odds)) / hill
            if log_value > math.log(np.finfo(float).max) or log_value < math.log(np.finfo(float).tiny):
                return no_warning("impossible inverse")
            value = float(math.exp(log_value))
            if diagnostic:
                value = -value if q < 0.0 else value
                reasons = [] if suppress_warnings else [
                    "signed diagnostic below fitted bottom"
                    if q < 0.0
                    else "signed diagnostic above fitted top"
                ]
            else:
                reasons = []
        except (ArithmeticError, ValueError, OverflowError):
            return no_warning("impossible inverse")
        if not math.isfinite(value):
            return no_warning("impossible inverse")
    if (
        not reasons
        and not suppress_warnings
        and (absorbance < calibrator_min - tolerance or absorbance > calibrator_max + tolerance)
    ):
        reasons.append("finite extrapolation")
    return InverseResult(value, tuple(reasons))


def make_metadata_lines(input_path: Path, metadata: dict[str, str]) -> list[str]:
    return [
        f"Source: {input_path.name}",
        f"Date: {metadata['date']}    Time: {metadata['time']}",
        f"Reader: {metadata['reader']}    Serial: {metadata['serial']}",
        f"Plate: {metadata['plate_type']}    Temperature: {metadata['temperature']}",
        f"Read: absorbance endpoint    Wavelength: {metadata['wavelength']}",
    ]


def set_common_page_style(fig: Any, page_number: int, total_pages: int) -> None:
    fig.text(
        0.5,
        0.018,
        f"Cytation BCA 562-nm report  ·  page {page_number} of {total_pages}",
        ha="center",
        va="bottom",
        fontsize=7,
        color="#666666",
    )


def add_page_header(fig: Any, title: str, subtitle: str = "") -> None:
    fig.text(0.055, 0.965, title, ha="left", va="top", fontsize=15, fontweight="bold", color="#17365d")
    if subtitle:
        fig.text(0.055, 0.935, subtitle, ha="left", va="top", fontsize=8.5, color="#444444")


def draw_heatmap_page(
    pdf: PdfPages,
    input_path: Path,
    metadata: dict[str, str],
    values: np.ndarray,
    raw_display: list[list[str]],
    page_number: int,
    total_pages: int,
) -> None:
    fig = plt.figure(figsize=A4)
    add_page_header(fig, "1. Raw absorbance plate", "96-well plate · 562 nm · values are unblank-corrected raw absorbance")
    y = 0.895
    for line in make_metadata_lines(input_path, metadata):
        fig.text(0.055, y, line, ha="left", va="top", fontsize=8, color="#333333")
        y -= 0.022

    ax = fig.add_axes([0.055, 0.36, 0.89, 0.39])
    ax.axis("off")
    cell_text_data = [[""] + [str(col) for col in COLS]]
    cell_text_data.extend([[row] + raw_display[i] for i, row in enumerate(ROWS)])
    table = ax.table(
        cellText=cell_text_data,
        cellLoc="center",
        colLoc="center",
        bbox=[0.0, 0.0, 1.0, 1.0],
        colWidths=[0.075] + [0.077] * 12,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.6)
    valid = values[np.isfinite(values)]
    vmin = float(np.min(valid))
    vmax = float(np.max(valid))
    cmap = LinearSegmentedColormap.from_list("white_blue", ["#f7fbff", "#08519c"])
    normalizer = Normalize(vmin=vmin, vmax=vmax if vmax > vmin else vmin + 1.0)
    for (row_index, col_index), cell in table.get_celld().items():
        cell.set_edgecolor("#777777")
        cell.set_linewidth(0.5)
        if row_index == 0 or col_index == 0:
            cell.set_facecolor("#d9e8f5")
            cell.get_text().set_color("#17365d")
            cell.get_text().set_fontweight("bold")
        else:
            value = values[row_index - 1, col_index - 1]
            if value >= OVERFLOW_OD:
                cell.set_facecolor("#fbb4ae")
                cell.get_text().set_color("#800026")
            else:
                colour = cmap(normalizer(value))
                cell.set_facecolor(colour)
                luminance = 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]
                cell.get_text().set_color("white" if luminance < 0.58 else "#1b1b1b")
    ax.set_title("Raw absorbance values", fontsize=9, loc="left", pad=8, color="#333333")

    cax = fig.add_axes([0.16, 0.29, 0.68, 0.018])
    scalar = cm.ScalarMappable(norm=normalizer, cmap=cmap)
    scalar.set_array(valid)
    colourbar = fig.colorbar(scalar, cax=cax, orientation="horizontal")
    colourbar.ax.tick_params(labelsize=7, length=2)
    colourbar.set_label("Absorbance colour scale (plate minimum to maximum)", fontsize=8, labelpad=3)
    fig.text(
        0.055,
        0.22,
        "Rows A–H and columns 1–12 are the source plate coordinates. Raw values retain their source decimal precision.",
        ha="left",
        va="top",
        fontsize=8,
        color="#444444",
    )
    set_common_page_style(fig, page_number, total_pages)
    pdf.savefig(fig)
    plt.close(fig)


def draw_curve_page(
    pdf: PdfPages,
    input_path: Path,
    metadata: dict[str, str],
    replicate_values: np.ndarray,
    means: np.ndarray,
    sds: np.ndarray,
    fit: FitResult,
    std_conc_ug_ml: np.ndarray,
    std_conc_ug_ul: np.ndarray,
    page_number: int,
    total_pages: int,
) -> None:
    fig = plt.figure(figsize=A4)
    add_page_header(
        fig,
        "2. 4PL calibration curve",
        "Equal-weighted duplicate means · raw absorbance without blank subtraction · concentrations converted from µg/mL to µg/µL",
    )
    ax = fig.add_axes([0.13, 0.39, 0.80, 0.49])
    max_c = float(np.max(std_conc_ug_ul))
    curve_x = np.linspace(0.0, max_c if max_c > 0 else 2.0, 600)
    curve_y = four_pl_model(
        curve_x, fit.bottom, fit.top, fit.ec50, fit.hill_slope
    )
    finite_curve = curve_y[np.isfinite(curve_y)]
    all_y = np.concatenate([replicate_values.ravel(), finite_curve])
    ymin = float(np.min(all_y))
    ymax = float(np.max(all_y))
    margin = max(0.03, (ymax - ymin) * 0.10)
    ax.plot(curve_x, curve_y, color="#1f77b4", linewidth=2.0, label="Fitted 4PL")
    for index, concentration in enumerate(std_conc_ug_ul):
        ax.scatter(
            [concentration, concentration],
            replicate_values[index],
            color="#777777",
            edgecolor="white",
            linewidth=0.5,
            s=30,
            zorder=3,
            label="Individual duplicates" if index == 0 else None,
        )
    ax.errorbar(
        std_conc_ug_ul,
        means,
        yerr=sds,
        fmt="D",
        color="#c23b22",
        markerfacecolor="#c23b22",
        markeredgecolor="white",
        markersize=5.5,
        capsize=3,
        linewidth=1,
        zorder=4,
        label="Mean ± sample SD",
    )
    for concentration, mean, nominal in zip(std_conc_ug_ul, means, std_conc_ug_ml):
        ax.annotate(
            f"{concentration:g}",
            (concentration, mean),
            xytext=(3, 5),
            textcoords="offset points",
            fontsize=6.5,
            color="#333333",
        )
    ax.set_xlim(0.0, max_c if max_c > 0 else 2.0)
    ax.set_ylim(ymin - margin, ymax + margin)
    ax.set_xlabel("Concentration (µg/µL)", fontsize=9)
    ax.set_ylabel("Absorbance at 562 nm", fontsize=9)
    ax.set_title("BCA standard response and fitted four-parameter logistic model", fontsize=10, color="#333333")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(loc="best", fontsize=7.5, frameon=True)
    fit_lines = [
        f"bottom = {fit.bottom:.6g}    top = {fit.top:.6g}",
        f"EC50 = {fit.ec50:.6g} µg/µL    Hill slope = {fit.hill_slope:.6g}",
        f"R² = {fit.r2:.6g}    RMSE = {fit.rmse:.6g} absorbance units",
        "4PL: y = bottom + (top − bottom) / (1 + (EC50 / x)^HillSlope); y(0) = bottom",
    ]
    y_text = 0.305
    for line in fit_lines:
        fig.text(0.10, y_text, line, ha="left", va="top", fontsize=8, family="DejaVu Sans", color="#333333")
        y_text -= 0.022
    if not fit.monotonic:
        fig.text(
            0.10,
            0.205,
            f"{WARNING_MARK} Calibration warning: standard means are not monotonic in increasing concentration order; fit retained.",
            ha="left",
            va="top",
            fontsize=8,
            color="#a33a00",
        )
    if not fit.physical_ascending:
        fig.text(
            0.10,
            0.18,
            f"{WARNING_MARK} Fit warning: fitted parameters do not describe a conventional ascending 4PL; inverse results are limited to mathematically defined wells.",
            ha="left",
            va="top",
            fontsize=8,
            color="#a33a00",
        )
    identifiability_warning = fit_identifiability_warning(fit, std_conc_ug_ul)
    if identifiability_warning:
        fig.text(
            0.10,
            0.155,
            f"{WARNING_MARK} Fit identifiability: {identifiability_warning}.",
            ha="left",
            va="top",
            fontsize=8,
            color="#a33a00",
        )
    set_common_page_style(fig, page_number, total_pages)
    pdf.savefig(fig)
    plt.close(fig)


def draw_well_table_page(
    pdf: PdfPages,
    input_path: Path,
    rows: list[tuple[str, str, str, tuple[str, ...]]],
    page_number: int,
    total_pages: int,
    first: bool,
) -> None:
    fig = plt.figure(figsize=A4)
    add_page_header(
        fig,
        "3. Estimated concentrations" if first else "3. Estimated concentrations (continued)",
        "Inverse 4PL estimate for every well · concentration unit: µg/µL · no dilution-factor correction",
    )
    if first:
        fig.text(
            0.055,
            0.895,
            f"Source: {input_path.name}",
            ha="left",
            va="top",
            fontsize=8,
            color="#444444",
        )
        fig.text(
            0.055,
            0.872,
            "Three columns: well coordinate  ·  raw absorbance  ·  estimated concentration",
            ha="left",
            va="top",
            fontsize=8,
            color="#444444",
        )
    headers = ["Well", "Raw absorbance", "Estimated concentration (µg/µL)"]
    table_data = [headers] + [[well, raw, concentration] for well, raw, concentration, _ in rows]
    ax = fig.add_axes([0.07, 0.15, 0.86, 0.69 if first else 0.78])
    ax.axis("off")
    table = ax.table(
        cellText=table_data,
        cellLoc="center",
        colLoc="center",
        bbox=[0, 0, 1, 1],
        colWidths=[0.16, 0.30, 0.54],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.0)
    for (row_index, col_index), cell in table.get_celld().items():
        cell.set_edgecolor("#888888")
        cell.set_linewidth(0.45)
        if row_index == 0:
            cell.set_facecolor("#d9e8f5")
            cell.get_text().set_fontweight("bold")
            cell.get_text().set_color("#17365d")
        else:
            cell.set_facecolor("#ffffff" if row_index % 2 else "#f7f9fb")
            if col_index == 2 and rows[row_index - 1][3]:
                cell.set_facecolor("#fff1c7")
                cell.get_text().set_color("#8a3b00")
    set_common_page_style(fig, page_number, total_pages)
    pdf.savefig(fig)
    plt.close(fig)


def draw_concentration_plate_page(
    pdf: PdfPages,
    input_path: Path,
    values: np.ndarray,
    concentration_values: np.ndarray,
    concentration_display: list[list[str]],
    page_number: int,
    total_pages: int,
    flags: list[tuple[str, str, str, tuple[str, ...]]],
    standard_coords_set: set[str],
    excluded_coords_set: set[str],
) -> None:
    """Render section 3 using the same 8 x 12 plate geometry as section 1."""
    fig = plt.figure(figsize=A4)
    add_page_header(
        fig,
        "3. Estimated concentrations",
        "96-well plate · inverse 4PL estimates · concentrations in µg/µL · no dilution-factor correction",
    )
    fig.text(
        0.055,
        0.895,
        f"Source: {input_path.name}    ·    Values correspond to the A–H / 1–12 well coordinates",
        ha="left",
        va="top",
        fontsize=8,
        color="#444444",
    )

    ax = fig.add_axes([0.055, 0.36, 0.89, 0.39])
    ax.axis("off")
    cell_text_data = [[""] + [str(col) for col in COLS]]
    cell_text_data.extend([[row] + concentration_display[i] for i, row in enumerate(ROWS)])
    table = ax.table(
        cellText=cell_text_data,
        cellLoc="center",
        colLoc="center",
        bbox=[0.0, 0.0, 1.0, 1.0],
        colWidths=[0.075] + [0.077] * 12,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.6)
    finite = concentration_values[np.isfinite(concentration_values)]
    if finite.size:
        cmin = float(np.min(finite))
        cmax = float(np.max(finite))
    else:
        cmin, cmax = 0.0, 1.0
    # Match section 1's blue table styling; the legend and normalization are
    # still based on estimated concentration rather than absorbance.
    cmap = LinearSegmentedColormap.from_list("white_blue", ["#f7fbff", "#08519c"])
    normalizer = Normalize(vmin=cmin, vmax=cmax if cmax > cmin else cmin + 1.0)
    flagged_coordinates = {item[0] for item in flags}
    for (row_index, col_index), cell in table.get_celld().items():
        cell.set_edgecolor("#777777")
        cell.set_linewidth(0.5)
        if row_index == 0 or col_index == 0:
            cell.set_facecolor("#d9e8f5")
            cell.get_text().set_color("#17365d")
            cell.get_text().set_fontweight("bold")
        else:
            value = concentration_values[row_index - 1, col_index - 1]
            raw_val = values[row_index - 1, col_index - 1]
            coordinate = f"{ROWS[row_index - 1]}{col_index}"
            is_standard = coordinate in standard_coords_set
            is_excluded = coordinate in excluded_coords_set
            
            if is_excluded or (not is_standard and raw_val < EMPTY_WELL_CUTOFF_OD):
                cell.set_facecolor("#ffffff")
                cell.get_text().set_color("#1b1b1b")
            elif raw_val >= OVERFLOW_OD:
                cell.set_facecolor("#fbb4ae")
                cell.get_text().set_color("#800026")
            elif math.isfinite(value):
                colour = cmap(normalizer(value))
                cell.set_facecolor(colour)
                luminance = 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]
                cell.get_text().set_color("white" if luminance < 0.58 else "#1b1b1b")
            else:
                cell.set_facecolor("#fff1c7")
                cell.get_text().set_color("#8a3b00")
            if coordinate in flagged_coordinates:
                cell.set_edgecolor("#b45f06")
                cell.set_linewidth(1.2)
    ax.set_title("Estimated concentration values", fontsize=9, loc="left", pad=8, color="#333333")

    cax = fig.add_axes([0.16, 0.29, 0.68, 0.018])
    scalar = cm.ScalarMappable(norm=normalizer, cmap=cmap)
    scalar.set_array(finite if finite.size else np.array([0.0]))
    colourbar = fig.colorbar(scalar, cax=cax, orientation="horizontal")
    colourbar.ax.tick_params(labelsize=7, length=2)
    colourbar.set_label("Concentration colour scale (finite estimates, µg/µL)", fontsize=8, labelpad=3)
    fig.text(
        0.055,
        0.22,
        "Same plate layout as section 1. Inverse-4PL estimates; flagged non-standard cells are outlined and marked with ⚠.",
        ha="left",
        va="top",
        fontsize=8,
        color="#444444",
    )
    fig.text(
        0.055,
        0.20,
        "Standard wells are sanity checks and are never caution-marked.",
        ha="left",
        va="top",
        fontsize=8,
        color="#444444",
    )
    fig.text(
        0.055,
        0.18,
        "Negative values are signed diagnostics, not physical concentrations. Empty/excluded wells are blank.",
        ha="left",
        va="top",
        fontsize=8,
        color="#444444",
    )
    set_common_page_style(fig, page_number, total_pages)
    pdf.savefig(fig)
    plt.close(fig)


def append_photos_to_pdf(pdf_path: Path, photo_paths: list[Path]) -> None:
    """Append image files as subsequent pages to an existing PDF report using pypdf and PIL/matplotlib."""
    if not photo_paths:
        return
    import io
    from pypdf import PdfReader, PdfWriter
    
    writer = PdfWriter()
    reader = PdfReader(str(pdf_path))
    for page in reader.pages:
        writer.add_page(page)
        
    for photo in photo_paths:
        if not photo.exists():
            continue
        fig = plt.figure(figsize=A4)
        ax = fig.add_axes([0.05, 0.05, 0.90, 0.90])
        ax.axis("off")
        try:
            img = Image.open(photo)
            ax.imshow(img)
            buf = io.BytesIO()
            fig.savefig(buf, format="pdf", bbox_inches="tight", pad_inches=0.1)
            plt.close(fig)
            buf.seek(0)
            img_pdf_reader = PdfReader(buf)
            for img_page in img_pdf_reader.pages:
                writer.add_page(img_page)
        except Exception as e:
            plt.close(fig)
            continue
            
    with open(pdf_path, "wb") as f:
        writer.write(f)


def draw_flags_pages(
    pdf: PdfPages,
    flags: list[tuple[str, str, str, tuple[str, ...]]],
    page_number_start: int,
    total_pages: int,
) -> int:
    if not flags:
        return page_number_start
    chunk_size = 30
    page_number = page_number_start
    for offset in range(0, len(flags), chunk_size):
        chunk = flags[offset : offset + chunk_size]
        fig = plt.figure(figsize=A4)
        add_page_header(
            fig,
            "Flags for section 3" if offset == 0 else "Flags for section 3 (continued)",
            f"{WARNING_MARK} appears in flagged concentration cells; finite extrapolations and signed diagnostics are reported.",
        )
        data = [["Well", "Raw absorbance", "Reported concentration", "Reason"]]
        for well, raw, concentration, reasons in chunk:
            data.append([well, raw, concentration, "; ".join(reasons)])
        # Keep the flags block compact: it is supplementary to the plate
        # table, so use ordinary table rows rather than stretching two rows
        # over nearly the full page.
        ax = fig.add_axes([0.055, 0.60, 0.89, 0.22])
        ax.axis("off")
        table = ax.table(
            cellText=data,
            cellLoc="left",
            colLoc="center",
            bbox=[0, 0, 1, 1],
            colWidths=[0.12, 0.22, 0.29, 0.37],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8.0)
        for (row_index, col_index), cell in table.get_celld().items():
            cell.set_edgecolor("#aa8a55")
            cell.set_linewidth(0.45)
            if row_index == 0:
                cell.set_facecolor("#ffe7a6")
                cell.get_text().set_fontweight("bold")
            else:
                cell.set_facecolor("#fffaf0" if row_index % 2 else "#fff3d4")
                cell.get_text().set_color("#6e3500")
        set_common_page_style(fig, page_number, total_pages)
        pdf.savefig(fig)
        plt.close(fig)
        page_number += 1
    return page_number


def build_report(
    input_path: Path,
    output_path: Path,
    standards_arg: Optional[str] = None,
    exclude_arg: Optional[str] = None,
    concentrations_arg: Optional[str] = None,
    photos: Optional[list[Path]] = None,
) -> dict[str, Any]:
    ws, candidate, metadata, wavelengths = choose_plate(input_path)
    values = candidate.values
    raw_display = [
        [display_number(ws.cell(candidate.start_row + r, candidate.start_col + c).value) for c in range(12)]
        for r in range(8)
    ]
    
    # Parse standard concentrations
    std_conc_ug_ml, std_conc_ug_ul = parse_concentrations_arg(concentrations_arg)

    # Parse standards pairs
    standard_pairs = parse_standards_arg(standards_arg)
    # standard_pairs is 8 tuples of (well1_name, well2_name)
    standard_coords_set = {w for pair in standard_pairs for w in pair}
    
    # Parse excluded coordinates
    excluded_wells = parse_exclude_arg(exclude_arg)
    excluded_coords_set = {f"{ROWS[r]}{c+1}" for r, c in excluded_wells}

    replicate_list = []
    for w1, w2 in standard_pairs:
        r1, c1 = parse_well_coordinate(w1)
        r2, c2 = parse_well_coordinate(w2)
        replicate_list.append([values[r1, c1], values[r2, c2]])
    replicate_values = np.asarray(replicate_list, dtype=float)

    means = np.mean(replicate_values, axis=1)
    sds = np.std(replicate_values, axis=1, ddof=1)
    fit = fit_4pl(replicate_values, std_conc_ug_ul)
    calibrator_min = float(np.min(means))
    calibrator_max = float(np.max(means))

    all_rows: list[tuple[str, str, str, tuple[str, ...]]] = []
    concentration_values = np.full(values.shape, np.nan, dtype=float)
    concentration_display: list[list[str]] = []
    flags: list[tuple[str, str, str, tuple[str, ...]]] = []
    for row_index, row_name in enumerate(ROWS):
        display_row: list[str] = []
        for col_index, col_number in enumerate(COLS):
            well = f"{row_name}{col_number}"
            raw_val = values[row_index, col_index]
            cell_raw_val = ws.cell(candidate.start_row + row_index, candidate.start_col + col_index).value
            raw = display_number(cell_raw_val)
            is_standard = well in standard_coords_set
            is_excluded = well in excluded_coords_set
            is_overflow = isinstance(cell_raw_val, str) and cell_raw_val.strip().upper() in ("OVRFLW", "OVERFLOW", ">4.0", "> 4.0", ">4")

            # If well is explicitly excluded, leave blank
            if is_excluded:
                display_row.append("")
                continue

            # If well is below empty cutoff and not a standard well, leave blank
            if not is_standard and not is_overflow and raw_val < EMPTY_WELL_CUTOFF_OD:
                display_row.append("")
                # Do not treat empty wells as errors/flags
                continue

            if is_overflow:
                concentration = f">2.000 {WARNING_MARK}" if not is_standard else ">2.000"
                inverse_reasons = ("absorbance detector overflow (OVRFLW > 4.0 OD)",)
                display_row.append(concentration)
                record = (well, raw, concentration, inverse_reasons)
                all_rows.append(record)
                if not is_standard:
                    flags.append(record)
                continue

            inverse = inverse_4pl(
                raw_val,
                fit,
                calibrator_min,
                calibrator_max,
                suppress_warnings=is_standard,
            )
            concentration = display_concentration(inverse, show_warning=not is_standard)
            if inverse.value is not None:
                concentration_values[row_index, col_index] = inverse.value
            display_row.append(concentration)
            record = (well, raw, concentration, inverse.reasons)
            all_rows.append(record)
            if inverse.reasons:
                flags.append(record)
        concentration_display.append(display_row)

    table_page_count = 1
    flags_page_count = math.ceil(len(flags) / 30) if flags else 0
    total_pages = 2 + table_page_count + flags_page_count
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(output_path) as pdf:
        info = pdf.infodict()
        info["Title"] = f"Cytation BCA 562-nm report - {input_path.name}"
        info["Author"] = "Hermes Agent"
        info["Subject"] = "96-well BCA absorbance and 4PL concentration report"
        draw_heatmap_page(pdf, input_path, metadata, values, raw_display, 1, total_pages)
        draw_curve_page(pdf, input_path, metadata, replicate_values, means, sds, fit, std_conc_ug_ml, std_conc_ug_ul, 2, total_pages)
        page_number = 3
        draw_concentration_plate_page(
            pdf,
            input_path,
            values,
            concentration_values,
            concentration_display,
            page_number,
            total_pages,
            flags,
            standard_coords_set,
            excluded_coords_set,
        )
        page_number += 1
        page_number = draw_flags_pages(pdf, flags, page_number, total_pages)

    if photos:
        append_photos_to_pdf(output_path, photos)

    warnings_list: list[str] = []
    if not fit.monotonic:
        warnings_list.append("standard means are non-monotonic")
    if not fit.physical_ascending:
        warnings_list.append("fit parameters are non-ascending/nonphysical")
    identifiability_warning = fit_identifiability_warning(fit, std_conc_ug_ul)
    if identifiability_warning:
        warnings_list.append(identifiability_warning)
    return {
        "input": str(input_path),
        "output": str(output_path),
        "sheet": ws.title,
        "plate_anchor": {"row": candidate.start_row, "column": candidate.start_col},
        "wavelength_mentions": wavelengths,
        "standard_coordinates": [
            list(pair) for pair in standard_pairs
        ],
        "excluded_wells": sorted(list(excluded_coords_set)),
        "standard_concentrations_ug_ml": std_conc_ug_ml.tolist(),
        "standard_concentrations_ug_ul": std_conc_ug_ul.tolist(),
        "standard_means": means.tolist(),
        "standard_sample_sd": sds.tolist(),
        "fit": {
            "bottom": fit.bottom,
            "top": fit.top,
            "ec50_ug_ul": fit.ec50,
            "hill_slope": fit.hill_slope,
            "r2": fit.r2,
            "rmse": fit.rmse,
            "monotonic": fit.monotonic,
            "physical_ascending": fit.physical_ascending,
            "optimizer_success": fit.optimizer_success,
            "optimizer_message": fit.optimizer_message,
        },
        "signed_diagnostic_definition": "For absorbance below fitted bottom or above fitted top, report the signed inverse-odds magnitude as a diagnostic; it is not a physical concentration.",
        "flagged_wells": [
            {"well": well, "raw_absorbance": raw, "reported": concentration, "reasons": list(reasons)}
            for well, raw, concentration, reasons in flags
        ],
        "warnings": warnings_list,
        "pages": total_pages,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Cytation5/Cytation 5 562-nm XLSX export")
    parser.add_argument("--output", type=Path, required=True, help="output PDF path")
    parser.add_argument("--standards", "-s", type=str, default=None, help="standard coordinate range (default: A1:B8 or C1:D8)")
    parser.add_argument("--exclude", "-e", type=str, default=None, help="wells to exclude (e.g. A1:B12, D1)")
    parser.add_argument("--concentrations", "-c", type=str, default=None, help="comma-separated 8 standard concentrations in ug/mL (default: 2000,1500,1000,750,500,250,125,0)")
    parser.add_argument("--photos", "-p", nargs="*", type=Path, default=[], help="optional photo/image file paths to append to the report PDF")
    args = parser.parse_args(argv)
    try:
        result = build_report(
            args.input,
            args.output,
            standards_arg=args.standards,
            exclude_arg=args.exclude,
            concentrations_arg=args.concentrations,
            photos=args.photos,
        )
    except (InputError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
