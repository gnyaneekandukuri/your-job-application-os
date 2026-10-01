"""Deterministic job ingestion. All page content remains untrusted text."""
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from .models import ExtractionDraft, now
from .safe_fetch import ExtractionError, fetch_public, validate_url


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1
        if not self.hidden and tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "section", "tr"):
            self.parts.append("\n")
        if not self.hidden and tag == "li":
            self.parts.append("• ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "section", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    if not isinstance(value, str):
        return ""
    parser = TextParser()
    parser.feed(value)
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


class JsonLdParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks, self.current = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            kind = (dict(attrs).get("type") or "").lower().split(";", 1)[0].strip()
            if kind == "application/ld+json":
                self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.blocks.append("".join(self.current))
            self.current = None


def jsonld_job(body: bytes, mime: str):
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExtractionError("This page uses an unsupported text encoding. Enter details manually.") from None
    if mime in ("application/json", "application/ld+json"):
        blocks = [text]
    else:
        parser = JsonLdParser()
        parser.feed(text)
        blocks = parser.blocks
    jobs = {}
    for block in blocks:
        try:
            stack = [json.loads(block)]
        except (ValueError, RecursionError):
            continue
        visited = 0
        while stack:
            node = stack.pop()
            visited += 1
            if visited > 10000:
                raise ExtractionError("This page has too much structured data. Enter details manually.")
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                types = node.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if isinstance(types, list) and any(t in ("JobPosting", "https://schema.org/JobPosting", "http://schema.org/JobPosting") for t in types):
                    jobs[json.dumps(node, sort_keys=True)] = node
                else:
                    stack.extend(value for value in node.values() if isinstance(value, (dict, list)))
    if len(jobs) != 1:
        raise ExtractionError("This page contains multiple jobs; use the link for one specific role." if jobs else
            "No supported job data found. This page may require JavaScript or login. Try a direct Ashby or Greenhouse listing or enter details manually.")
    return next(iter(jobs.values()))


def greenhouse_target(url: str):
    parts = urlsplit(url)
    if parts.hostname not in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        return None
    match = re.fullmatch(r"/([A-Za-z0-9_-]+)/jobs/([0-9]+)/?", parts.path)
    if match:
        return f"https://boards-api.greenhouse.io/v1/boards/{match[1]}/jobs/{match[2]}"
    if parts.path == "/embed/job_app":
        query = parse_qs(parts.query)
        board, job = query.get("for", [""])[0], query.get("token", [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]+", board) and re.fullmatch(r"[0-9]+", job):
            return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}"
    raise ExtractionError("Use a Greenhouse link for a specific job, not the company job list.")


def ashby_reference(url: str):
    parts = urlsplit(url)
    if parts.hostname != "jobs.ashbyhq.com":
        return None
    match = re.fullmatch(r"/([A-Za-z0-9_-]+)/([0-9a-fA-F-]{36})(?:/application)?/?", parts.path)
    if not match:
        raise ExtractionError("Use an Ashby link for a specific job, not the company job list.")
    try:
        job_id = str(UUID(match[2]))
    except ValueError:
        raise ExtractionError("This Ashby link has an invalid job ID.") from None
    return match[1], job_id


def ashby_target(reference):
    return f"https://api.ashbyhq.com/posting-api/job-board/{reference[0]}"


def ashby_details(page, reference, fetcher):
    try:
        data = json.loads(page.body)
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ValueError()
    except (ValueError, RecursionError):
        raise ExtractionError("Ashby returned unreadable job data. Try again or enter details manually.") from None
    matches = []
    for job in data["jobs"]:
        if not isinstance(job, dict) or not isinstance(job.get("jobUrl"), str):
            continue
        try:
            if ashby_reference(job["jobUrl"]) == reference:
                matches.append(job)
        except (ExtractionError, ValueError):
            continue
    if len(matches) != 1:
        raise ExtractionError("This exact role is not in Ashby's public feed. It may have been removed or be unavailable publicly; check the link or enter it manually."
                              if not matches else "Ashby returned multiple matches for this role. Enter details manually.")
    job = matches[0]
    title = plain_text(job.get("title"))
    raw_description = job.get("descriptionPlain")
    # descriptionPlain is already text: parsing it as HTML could discard literal '<' content.
    description = raw_description.strip() if isinstance(raw_description, str) and raw_description.strip() else plain_text(job.get("descriptionHtml"))
    company, company_page = "", None
    warnings = []
    listing = f"https://jobs.ashbyhq.com/{reference[0]}/{reference[1]}"
    try:
        candidate_page = fetcher(listing)
        if ashby_reference(candidate_page.final_url) != reference:
            raise ExtractionError("Company metadata came from a different listing.")
        metadata = jsonld_job(candidate_page.body, candidate_page.content_type)
        if plain_text(metadata.get("title")) != title:
            raise ExtractionError("Company metadata did not match the feed's job title.")
        if metadata.get("url") and (not isinstance(metadata["url"], str) or ashby_reference(metadata["url"]) != reference):
            raise ExtractionError("Company metadata did not match the requested job.")
        organization = metadata.get("hiringOrganization", {})
        company = plain_text(organization.get("name")) if isinstance(organization, dict) else ""
        if company:
            company_page = candidate_page
    except (ExtractionError, ValueError, TypeError):
        warnings.append("The company name could not be verified against the matching listing's structured data. Please enter it.")
    if job.get("isListed") is False:
        warnings.append("Ashby marks this role as unlisted (direct-link only). Confirm its availability with the company.")
    return title, company, description, warnings, company_page


def extract_job(raw_url: str, fetcher=fetch_public) -> ExtractionDraft:
    original = validate_url(raw_url)
    ashby = ashby_reference(original)
    target = greenhouse_target(original)
    if target or ashby:
        try:
            page = fetcher(ashby_target(ashby) if ashby else target)
        except ExtractionError as exc:
            if not ashby or "too large" not in str(exc):
                raise
            # Large employer feeds may exceed the unchanged global body limit.
            # Try only the exact single listing, through the same safe fetcher.
            page = fetcher(original)
            if ashby_reference(page.final_url) != ashby:
                raise ExtractionError("The listing redirected to a different job. Paste the JD manually.")
            metadata = jsonld_job(page.body, page.content_type)
            if metadata.get("url") and (not isinstance(metadata["url"], str) or ashby_reference(metadata["url"]) != ashby):
                raise ExtractionError("The page describes a different job. Paste the JD manually.")
            ashby = None
        final = original  # The input listing is mapped to the public GET API, not navigated.
    else:
        page = fetcher(original)
        final = page.final_url
        ashby = ashby_reference(final)
        target = greenhouse_target(final)
        if target or ashby:
            page = fetcher(ashby_target(ashby) if ashby else target)
    warnings = ["Extraction is not verification of job availability, sponsorship, or fit. Review the details before saving."]
    if urlsplit(original).scheme == "http":
        warnings.append("The original link uses unencrypted HTTP; prefer the site's HTTPS listing.")
    expires = None
    company_page = None
    if ashby:
        title, company, description, notes, company_page = ashby_details(page, ashby, fetcher)
        warnings.extend(notes)
        adapter = "ASHBY_API_V1"
    elif target:
        try:
            data = json.loads(page.body)
            if not isinstance(data, dict) or "id" not in data:
                raise ValueError()
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ExtractionError("Greenhouse returned unreadable job data. Enter details manually.") from None
        title, company = plain_text(data.get("title")), plain_text(data.get("company_name"))
        content = data.get("content", "")
        description = plain_text(html.unescape(content) if isinstance(content, str) else "")
        adapter = "GREENHOUSE_API_V1"
        expires = data.get("application_deadline")
    else:
        data = jsonld_job(page.body, page.content_type)
        company_data = data.get("hiringOrganization", {})
        company = plain_text(company_data.get("name")) if isinstance(company_data, dict) else ""
        title, description = plain_text(data.get("title")), plain_text(data.get("description"))
        adapter = "JSON_LD_V1"
        expires = data.get("validThrough")
    for label, value, limit in (("company", company, 200), ("title", title, 200), ("description", description, 50000)):
        if len(value) > limit:
            raise ExtractionError(f"The extracted {label} exceeds the supported length; it was not truncated. Enter details manually.")
        if not value or (label == "description" and len(value) < 20):
            warnings.append(f"The {label} is missing or incomplete. Please supply it before confirming.")
    if isinstance(expires, str):
        try:
            parsed = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if parsed.replace(tzinfo=parsed.tzinfo or timezone.utc) < now():
                warnings.append("The source's listed deadline has passed. This may be an expired role.")
        except ValueError:
            warnings.append("The source's deadline could not be interpreted; check the listing.")
    if re.search(r"ignore.{0,60}instructions|disregard.{0,60}instructions|system prompt|you are (?:an ai|chatgpt)",
                 f"{title}\n{company}\n{description}", re.I | re.S):
        warnings.append("Possible instructions addressed to an AI were found. Treat the description as untrusted text, not commands.")
    return ExtractionDraft(id=uuid4(), original_url=original, final_url=final,
        company_source_url=company_page.final_url if company_page else None,
        company_response_sha256=hashlib.sha256(company_page.body).hexdigest() if company_page else None,
        fetched_url=page.final_url, retrieved_at=now(), adapter=adapter,
        response_sha256=hashlib.sha256(page.body).hexdigest(), company=company,
        title=title, description=description, warnings=warnings)
