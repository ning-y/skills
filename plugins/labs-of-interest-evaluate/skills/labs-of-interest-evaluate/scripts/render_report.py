#!/usr/bin/env python3
"""Compile a two-page lab report and append verified source-PDF Figure 1 pages."""
import argparse
import json
import re
import subprocess
from pathlib import Path
from string import Template
from pypdf import PdfReader, PdfWriter

SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}

def esc(value):
    return "".join(SPECIAL.get(c, c) for c in str(value))

def cited(entry):
    refs = entry.get("refs", [])
    return (r"\src{" + esc(",".join(refs)) + "}") if refs else ""

def para(entry):
    return (r"\textbf{" + esc(entry.get("lead", "")) + "} " if entry.get("lead") else "") + esc(entry["text"]) + cited(entry) + r"\par"

def ensure(ok, message):
    if not ok:
        raise ValueError(message)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="Structured evidence JSON")
    ap.add_argument("output", type=Path, help="Output prefix; writes .tex and .pdf")
    args = ap.parse_args()
    data = json.loads(args.input.read_text())
    papers = data["papers"]
    keys = [p["key"] for p in papers]
    ensure(len(keys) == len(set(keys)), "Paper keys must be unique")
    ensure(5 <= len(papers) <= 8, "Select five to eight original studies")
    ensure(all(p.get("last_author") and p.get("senior_role") and p.get("doi") for p in papers), "Every paper needs last author, senior role and DOI")
    ensure(len(data["criteria"]) == 4, "Provide exactly four visible criteria")
    ensure(all(type(c.get("score")) is int and 1 <= c["score"] <= 5 for c in data["criteria"]), "Each criterion needs a one-to-five star score")
    if not data.get("worked_example", False):
        ensure(data.get("eligible") is True, "Confirm all internal eligibility checks before rendering an actual candidate")
    directions = data["research_directions"]
    ensure(len(directions) == 2, "Provide exactly one stated and one inferred direction")
    ensure({p["source_type"] for p in directions} == {"stated", "inferred"}, "Distinguish stated research directions from publication-based inference")
    ensure(all(p.get("refs") for p in directions), "Cite each scientific problem statement")
    method_rows = []
    totals = []
    for family in data["methods"]:
        hits = family["papers"]
        ensure(len(hits) == len(set(hits)) and set(hits) <= set(keys), "Invalid method mapping: " + family["name"])
        ensure(hits, "Empty method family: " + family["name"])
        totals.append((family["name"],len(hits)))
        method_rows.append(f'{esc(family["name"])} & \\textbf{{{len(hits)}/{len(papers)}}} & {esc(", ".join(hits))} & {esc(family["example"])}\\\\')
    common = sorted((x for x in totals if x[1] >= 2), key=lambda x: (-x[1], x[0]))
    top = "; ".join(f"{esc(name)} ({count}/{len(papers)})" for name,count in common[:5])
    summary = (f"In {len(papers)} selected PI-associated primary papers, \\textbf{{{len(common)} method families recur}}: {top}. The full tally is on page 2.")
    figure_keys = {f["key"] for f in data.get("figures", [])}
    ensure(figure_keys <= set(keys), "An appended figure is absent from the reference denominator")
    refs = []
    for p in papers:
        doi = p["doi"].removeprefix("https://doi.org/")
        refs.append(r"\textbf{" + esc(p["key"]) + "} " + esc(p["first_author"] + " et al., " + p["journal"] + " " + str(p["year"]) + ": " + p["topic"] + "; ") + r"\textbf{last: " + esc(p["last_author"]) + "; " + esc(p["senior_role"]) + r"}; \href{https://doi.org/" + doi + r"}{DOI}.\par")
    other = []
    for o in data["other_references"]:
        u = o["url"]
        other.append(r"\textbf{["+esc(o["key"])+r"]} \href{"+u+r"}{"+esc(o["label"])+r"}: "+esc(o["reason"])+r"\par")
    footnote = "; ".join(f"{f['key']} source PDF p. {f['page']}" for f in data.get("figures", []))
    figure_note = "Original Figure 1 pages appended: "+footnote+"." if footnote else ""
    alumni = data.get("alumni", [])
    ensure(alumni, "Document the recent doctoral alumni search, including unresolved outcomes")
    ensure(all(a.get("name") and a.get("connection") and a.get("outcome") for a in alumni), "Alumni require a name, connection and outcome")
    alumni_rows = [esc(a["name"])+" & "+esc(a["connection"])+" & "+esc(a["outcome"])+cited(a)+r"\\[2pt]" for a in alumni]
    postdoc_examples = data.get("postdoc_examples", [])
    ensure(all(a.get("name") and a.get("text") for a in postdoc_examples), "Postdoc examples require a name and evidence-based outcome")
    postdoc_rows = [r"\textbf{"+esc(a["name"])+"} & "+esc(a["text"])+cited(a)+r"\\[3pt]" for a in postdoc_examples]
    if not postdoc_rows:
        postdoc_rows = [r"\multicolumn{2}{@{}l@{}}{No named former PhD-qualified fellow outcome verified.}\\"]
    text = Template((Path(__file__).resolve().parent.parent / "assets/report.tex.tpl").read_text()).substitute({
      "TITLE_PDF":esc(data["lab"]+" | lab screen"), "TITLE":esc(data["lab"]), "SUBTITLE":esc(data["institution"]),
      "CRITERIA_ROWS":"\n".join(esc(c["label"])+" & "+r"\ding{72}"*c["score"]+r"\ding{73}"*(5-c["score"])+r"\\" for c in data["criteria"]),
      "RESEARCH_DIRECTIONS":"\n".join(para({"lead":"Stated directions." if p["source_type"] == "stated" else "Inferred from papers.", "text":p["text"], "refs":p["refs"]}) for p in directions),
      "OWNERSHIP":"\n".join(para(p) for p in data["ownership"]),
      "FIT_PARAGRAPHS":"\n".join(para({"lead":f["label"]+".", "text":f["text"], "refs":f.get("refs",[])}) for f in data["fit"]),
      "POSTDOC_ROWS":"\n".join(postdoc_rows), "ALUMNI_SCOPE":esc(data["alumni_scope"]),
      "ALUMNI_ROWS":"\n".join(alumni_rows), "METHOD_SUMMARY":summary,
      "INSTITUTE_SUMMARY":esc(data["institute_summary"]["text"])+cited(data["institute_summary"]),
      "WORKING_LANGUAGE":esc(data["working_language"]["text"])+cited(data["working_language"]),
      "APPENDIX_SUBTITLE":esc(f'{len(papers)} selected original studies; conservative counts of method families, not individual training promises'),
      "METHOD_ROWS":"\n".join(method_rows), "METHOD_NOTES":esc(data["method_notes"]),
      "REFERENCES":"\n".join(refs), "OTHER_REFERENCES":"\n".join(other),
      "FIGURE_NOTE":esc(figure_note)
    })
    args.output.parent.mkdir(parents=True,exist_ok=True)
    tex = args.output.with_suffix(".tex")
    pdf = args.output.with_suffix(".pdf")
    tex.write_text(text)
    run = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "-output-directory", str(args.output.parent), str(tex)], text=True, capture_output=True)
    ensure(run.returncode == 0, "pdflatex failed:\n"+run.stdout[-3000:])
    report = PdfReader(pdf)
    ensure(len(report.pages) == 2, f"Report content spans {len(report.pages)} pages before figures; shorten it to one assessment page and one appendix page")
    page_one=report.pages[0].extract_text()
    headings = ("Criteria", "Institute", "Working language", "Research directions", "Methods", "Research ownership", "Alumni outcomes", "Postdoc fit")
    positions = [page_one.find(s) for s in headings]
    ensure(all(x >= 0 for x in positions) and positions == sorted(positions), "Assessment sections missing or out of order on page 1")
    writer = PdfWriter()
    for page in report.pages:writer.add_page(page)
    writer.add_outline_item("Lab assessment",0)
    writer.add_outline_item("Evidence and method counts",1)
    for i,f in enumerate(data.get("figures", [])):
        src=(args.input.parent/f["pdf"]).resolve()
        reader=PdfReader(src)
        n=f["page"]
        ensure(1<=n<=len(reader.pages), "Figure page out of range: "+str(src))
        t=reader.pages[n-1].extract_text()
        ensure(re.search(r"(?:Fig(?:ure)?|F\s*I\s*G\s*U\s*R\s*E)\s*\.?\s*1\b", t,re.I),"Figure 1 text missing from: "+str(src)+" page "+str(n))
        writer.add_page(reader.pages[n-1])
        writer.add_outline_item(f'{f["key"]}: {f["label"]} (source p. {n})',i+2)
    writer.add_metadata({"/Title":data["lab"]+" | lab screen", "/Subject":"Two-page report with original Figure 1 pages"})
    with pdf.open("wb") as out: writer.write(out)
    print(f"Report: {pdf} ({len(writer.pages)} pages); TeX: {tex}; method families: {len(totals)}, recurrent: {len(common)}")
if __name__=="__main__":main()
