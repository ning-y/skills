#!/usr/bin/env python3
"""Recall-oriented PubMed affiliation search. Human adjudication is mandatory.

Example: python3 scripts/pubmed_scan.py --as-of 2026-09-27 --output /tmp/pubmed.json
No third-party Python dependencies. The caller decides the user's local date.
"""

import argparse
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


# Each key is one allowed institute; these terms are intentionally broad and
# produce false positives. Check last/co-senior author affiliations manually.
ALIASES = {
    "Wellcome Sanger Institute": ["Wellcome Sanger Institute", "Sanger Institute"],
    "Francis Crick Institute": ["Francis Crick Institute"],
    "DKFZ": ["German Cancer Research Center", "German Cancer Research Centre", "DKFZ", "Deutsches Krebsforschungszentrum"],
    "RIKEN IMS": ["RIKEN Center for Integrative Medical Sciences", "RIKEN Centre for Integrative Medical Sciences", "RIKEN IMS"],
    "EMBL Heidelberg": ["EMBL Heidelberg", "European Molecular Biology Laboratory"],
    "Netherlands Cancer Institute": ["Netherlands Cancer Institute", "Nederlands Kanker Instituut"],
    "CeMM": ["CeMM Research Center for Molecular Medicine", "CeMM Research Centre for Molecular Medicine", "Research Center for Molecular Medicine of the Austrian Academy of Sciences"],
    "Institut Pasteur Paris": ["Institut Pasteur"],
    "IRB Barcelona": ["IRB Barcelona", "Institute for Research in Biomedicine Barcelona"],
    "ICR London": ["Institute of Cancer Research"],
    "WEHI": ["Walter and Eliza Hall Institute", "WEHI"],
    "Garvan Institute": ["Garvan Institute of Medical Research"],
    "SickKids Research Institute": ["SickKids Research Institute", "Hospital for Sick Children"],
    "IFReC": ["Immunology Frontier Research Center", "IFReC"],
    "IMSUT": ["Institute of Medical Science, University of Tokyo", "IMSUT"],
    "CiRA": ["Center for iPS Cell Research and Application", "Centre for iPS Cell Research and Application", "CiRA"],
    "Japan National Cancer Center Research Institute": ["National Cancer Center Research Institute", "National Cancer Centre Research Institute"],
    "CNIO": ["Spanish National Cancer Research Centre", "Spanish National Cancer Research Center", "CNIO"],
    "MRC LMB": ["MRC Laboratory of Molecular Biology"],
    "MRC LMS": ["MRC Laboratory of Medical Sciences"],
    "Babraham Institute": ["Babraham Institute"],
    "Friedrich Miescher Institute": ["Friedrich Miescher Institute"],
    "IMP Vienna": ["Research Institute of Molecular Pathology"],
    "IMBA Vienna": ["Institute of Molecular Biotechnology", "IMBA"],
    "Hubrecht Institute": ["Hubrecht Institute"],
    "CRG Barcelona": ["Centre for Genomic Regulation", "Center for Genomic Regulation"],
    "FIMM": ["Institute for Molecular Medicine Finland", "FIMM"],
    "RIKEN BDR": ["RIKEN Center for Biosystems Dynamics Research", "RIKEN Centre for Biosystems Dynamics Research"],
    "ASHBi": ["Institute for the Advanced Study of Human Biology", "ASHBi"],
    "JFCR Cancer Institute": ["Cancer Institute, Japanese Foundation for Cancer Research", "Japanese Foundation for Cancer Research", "JFCR"],
    "Peter MacCallum": ["Peter MacCallum Cancer Centre", "Peter MacCallum Cancer Center"],
    "QIMR Berghofer": ["QIMR Berghofer Medical Research Institute", "QIMR Berghofer"],
    "Lunenfeld-Tanenbaum": ["Lunenfeld-Tanenbaum Research Institute"],
    "Institut Curie Research Center": ["Institut Curie"],
    "Weizmann Institute": ["Weizmann Institute of Science"],
    "MPI-CBG": ["Max Planck Institute of Molecular Cell Biology and Genetics", "MPI-CBG"],
    "Max Planck Institute of Biochemistry": ["Max Planck Institute of Biochemistry", "Max-Planck-Institute of Biochemistry"],
}
assert len(ALIASES) == 37
BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def make_query(start, end):
    aliases = dict.fromkeys(term for terms in ALIASES.values() for term in terms)
    affiliation = " OR ".join(f'"{term}"[Affiliation]' for term in aliases)
    return f'("{start:%Y/%m/%d}"[Date - Publication] : "{end:%Y/%m/%d}"[Date - Publication]) AND ({affiliation})'


class NCBI:
    def __init__(self):
        self.last_request = 0.0
        self.api_key = os.environ.get("NCBI_API_KEY", "")
        self.email = os.environ.get("NCBI_EMAIL", "")

    def get(self, endpoint, params):
        params = dict(params)
        params["tool"] = "labs-of-interest-discovery"
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["email"] = self.email
        url = BASE + endpoint + "?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            # Stay below NCBI's unauthenticated limit of three requests/second.
            delay = 0.4 - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "labs-of-interest-discovery/0.1"}), timeout=45
                ) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 5:
                    raise
                retry_after = exc.headers.get("Retry-After", "")
                time.sleep(int(retry_after) if retry_after.isdigit() else min(2 ** attempt, 30))
            except (urllib.error.URLError, TimeoutError):
                if attempt == 5:
                    raise
                time.sleep(min(2 ** attempt, 30))
        raise RuntimeError("Unreachable")


def value(node):
    return "" if node is None else "".join(node.itertext()).strip()


def ymd(node):
    if node is None:
        return ""
    parts = [value(node.find(part)) for part in ("Year", "Month", "Day")]
    if not parts[0]:
        return ""
    for index in (1, 2):
        if parts[index].isdigit():
            parts[index] = f"{int(parts[index]):02d}"
    return "-".join(part for part in parts if part)


def parse_record(node):
    article = node.find("./MedlineCitation/Article")
    citation = node.find("./MedlineCitation")
    if article is None or citation is None:
        raise ValueError("Expected a PubmedArticle with MedlineCitation and Article")
    author_list = []
    for author in article.findall("./AuthorList/Author"):
        name = " ".join(filter(None, (value(author.find("ForeName")), value(author.find("LastName")))))
        author_list.append({
            "name": name or value(author.find("CollectiveName")),
            "affiliations": [value(aff) for aff in author.findall("./AffiliationInfo/Affiliation")],
        })
    dates = []
    for date in article.findall("./ArticleDate"):
        dates.append({"source": "ArticleDate", "type": date.get("DateType", ""), "date": ymd(date)})
    for date in node.findall("./PubmedData/History/PubMedPubDate"):
        dates.append({"source": "History", "type": date.get("PubStatus", ""), "date": ymd(date)})
    issue = article.find("./Journal/JournalIssue/PubDate")
    if issue is not None:
        dates.append({"source": "JournalIssue", "type": "print_or_issue", "date": value(issue.find("MedlineDate")) or ymd(issue)})
    ids = {identifier.get("IdType", ""): value(identifier)
           for identifier in node.findall("./PubmedData/ArticleIdList/ArticleId")}
    return {
        "pmid": value(citation.find("PMID")),
        "title": value(article.find("ArticleTitle")),
        "journal": value(article.find("./Journal/Title")),
        "abstract": " ".join(filter(None, (value(x) for x in article.findall("./Abstract/AbstractText")))),
        "publication_types": [value(x) for x in article.findall("./PublicationTypeList/PublicationType")],
        "dates": dates,
        "doi": ids.get("doi", ""),
        "pmcid": ids.get("pmc", ""),
        "authors": author_list,
        "last_author": author_list[-1] if author_list else None,
    }


def scan(as_of, output, dry_run=False):
    start = as_of - dt.timedelta(days=6)
    query = make_query(start, as_of)
    if dry_run:
        print(json.dumps({"from": start.isoformat(), "through": as_of.isoformat(), "institutes": len(ALIASES), "query": query}, indent=2))
        return
    api = NCBI()
    first = json.loads(api.get("esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "json", "retmax": 0}))
    count = int(first["esearchresult"]["count"])
    if count > 10000:
        raise RuntimeError("PubMed ESearch has a 10,000-ID retrieval ceiling; split the query by institute or date before proceeding")
    pmids = []
    for offset in range(0, count, 500):
        data = json.loads(api.get("esearch.fcgi", {
            "db": "pubmed", "term": query, "retmode": "json", "retstart": offset, "retmax": 500,
        }))
        pmids.extend(data["esearchresult"]["idlist"])
    if len(pmids) != count or len(set(pmids)) != count:
        raise RuntimeError(f"Search results shifted during pagination: expected {count}, found {len(pmids)} IDs ({len(set(pmids))} unique); rerun")
    records = []
    for offset in range(0, count, 100):
        ids = pmids[offset:offset + 100]
        xml = api.get("efetch.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
        records.extend(parse_record(node) for node in ET.fromstring(xml).findall("PubmedArticle"))
    if {record["pmid"] for record in records} != set(pmids):
        raise RuntimeError("EFetch record IDs do not match ESearch; refuse to write an incomplete audit")
    payload = {
        "searched_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "start": start.isoformat(), "end": as_of.isoformat(),
        "institutes_queried": list(ALIASES), "query": query,
        "raw_hit_count": count, "records": records,
        "note": "Recall-oriented ANY-author affiliation matches; verify first publication, primary status, senior author's current institute, disease and wet/dry by hand.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{count} raw PubMed hits saved to {output}; manual gate audit required")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=dt.date.fromisoformat, required=True,
                        help="YYYY-MM-DD in the user's time zone; search this day plus six preceding calendar days")
    parser.add_argument("--output", type=Path, help="JSON output path (required except with --dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Print the query without contacting PubMed")
    args = parser.parse_args()
    if not args.dry_run and args.output is None:
        parser.error("--output is required unless --dry-run is set")
    scan(args.as_of, args.output, args.dry_run)


if __name__ == "__main__":
    main()
