import json
import os
import re
import uuid
from difflib import SequenceMatcher

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3
from datetime import datetime
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
from PIL import Image
import imagehash

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024

DATABASE = "risk_platform.db"
UPLOAD_FOLDER = os.path.join(app.root_path, "uploads")
ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def connect_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS threats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            platform TEXT NOT NULL,
            url TEXT NOT NULL,
            risk_score INTEGER NOT NULL,
            status TEXT NOT NULL,
            reasons TEXT,
            created_at TEXT NOT NULL,
            candidate_type TEXT NOT NULL DEFAULT 'social',
            developer TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            logo_match INTEGER NOT NULL DEFAULT 0,
            is_flagged INTEGER NOT NULL DEFAULT 1
        )
    """)

    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(threats)")
    }
    new_columns = {
        "candidate_type": "TEXT NOT NULL DEFAULT 'social'",
        "developer": "TEXT NOT NULL DEFAULT ''",
        "description": "TEXT NOT NULL DEFAULT ''",
        "logo_match": "INTEGER NOT NULL DEFAULT 0",
        "is_flagged": "INTEGER NOT NULL DEFAULT 1"
    }
    for column, definition in new_columns.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE threats ADD COLUMN {column} {definition}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS brand_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            brand_name TEXT NOT NULL DEFAULT '',
            logo_path TEXT NOT NULL DEFAULT '',
            official_website TEXT NOT NULL DEFAULT '',
            social_accounts TEXT NOT NULL DEFAULT '[]',
            applications TEXT NOT NULL DEFAULT '[]',
            identifiers TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("INSERT OR IGNORE INTO brand_profile (id) VALUES (1)")

    conn.commit()
    conn.close()


def parse_json_list(value):
    try:
        result = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return result if isinstance(result, list) else []


def get_brand_profile(conn):
    row = conn.execute("SELECT * FROM brand_profile WHERE id = 1").fetchone()
    profile = dict(row)
    profile["social_accounts"] = parse_json_list(profile["social_accounts"])
    profile["applications"] = parse_json_list(profile["applications"])
    profile["identifiers"] = parse_json_list(profile["identifiers"])
    return profile


def clean_string_list(value):
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def normalized_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def name_variation(official_name, candidate_name):
    official = normalized_name(official_name)
    candidate = normalized_name(candidate_name)
    if not official or not candidate:
        return None

    if official == candidate:
        if str(official_name).strip().lower() != str(candidate_name).strip().lower():
            return "Spacing or punctuation change"
        return None

    candidate_tokens = [normalized_name(token) for token in str(candidate_name).split()]
    if official in candidate_tokens:
        return "Added word to the official name"
    if len(candidate) > len(official) and candidate.startswith(official):
        return "Added word or text to the official name"

    if len(official) == len(candidate):
        mismatches = [
            index for index, (left, right) in enumerate(zip(official, candidate))
            if left != right
        ]
        if (
            len(mismatches) == 2
            and mismatches[1] == mismatches[0] + 1
            and official[mismatches[0]] == candidate[mismatches[1]]
            and official[mismatches[1]] == candidate[mismatches[0]]
        ):
            return "Adjacent character rearrangement"
        if len(mismatches) == 1:
            return "One-character spelling change"

    if abs(len(official) - len(candidate)) == 1:
        shorter, longer = sorted((official, candidate), key=len)
        left = right = 0
        skipped = False
        while left < len(shorter) and right < len(longer):
            if shorter[left] == longer[right]:
                left += 1
                right += 1
            elif skipped:
                break
            else:
                skipped = True
                right += 1
        if left == len(shorter):
            return "Extra character" if len(candidate) > len(official) else "Missing character"

    if SequenceMatcher(None, official, candidate).ratio() >= 0.72:
        return "Close spelling variation"
    return None


def normalize_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/").lower()
    return host + path


def is_within_registered_site(candidate_url, official_url):
    candidate = urlparse(candidate_url if "://" in candidate_url else "https://" + candidate_url)
    official = urlparse(official_url if "://" in official_url else "https://" + official_url)
    candidate_host = (candidate.hostname or "").lower().removeprefix("www.")
    official_host = (official.hostname or "").lower().removeprefix("www.")
    if not official_host or not (
        candidate_host == official_host
        or candidate_host.endswith("." + official_host)
    ):
        return False

    official_path = official.path.rstrip("/").lower()
    candidate_path = candidate.path.rstrip("/").lower()
    return (
        not official_path
        or candidate_path == official_path
        or candidate_path.startswith(official_path + "/")
    )


def is_registered_official(profile, candidate_type, name, url, developer):
    candidate_url = normalize_url(url)
    if (
        candidate_type == "website"
        and profile["official_website"]
        and is_within_registered_site(url, profile["official_website"])
    ):
        return True

    account_urls = {
        normalize_url(account.get("url", "") if isinstance(account, dict) else account)
        for account in profile["social_accounts"]
    }
    app_urls = {
        normalize_url(application.get("url", ""))
        for application in profile["applications"]
        if isinstance(application, dict)
    }
    if candidate_type == "social" and candidate_url in account_urls:
        return True
    if candidate_type == "app":
        if candidate_url in app_urls:
            return True
        candidate_name = normalized_name(name)
        candidate_developer = normalized_name(developer)
        for application in profile["applications"]:
            if not isinstance(application, dict):
                continue
            if (
                candidate_name
                and candidate_name == normalized_name(application.get("name", ""))
                and candidate_developer
                and candidate_developer == normalized_name(application.get("developer", ""))
            ):
                return True
    return False


def assess_candidate(profile, candidate_type, name, url, developer, description, logo_match):
    score = 0
    reasons = []
    reference_names = [profile["brand_name"]]
    if candidate_type == "app":
        reference_names.extend(
            application.get("name", "")
            for application in profile["applications"]
            if isinstance(application, dict)
        )

    variations = []
    for reference in filter(None, reference_names):
        variation = name_variation(reference, name)
        if not variation:
            variation = next((
                token_variation
                for token in str(name).split()
                if (token_variation := name_variation(reference, token))
            ), None)
        if variation:
            variations.append(variation)
    if variations:
        reason = "Name resembles a registered brand: " + variations[0]
        reasons.append(reason)
        score += 45
    elif normalized_name(name) in {
        normalized_name(reference) for reference in reference_names if reference
    }:
        reasons.append("Uses the registered brand name on an unregistered identity")
        score += 45

    searchable_text = (name + " " + description).lower()
    identifier_matches = [
        identifier for identifier in profile["identifiers"]
        if len(identifier) >= 3 and identifier.lower() in searchable_text
    ]
    if identifier_matches:
        reasons.append("Uses registered brand identifier: " + ", ".join(identifier_matches))
        score += 25

    if logo_match:
        reasons.append("Submitted for review because its logo appears similar to the official logo")
        score += 35

    impersonation_terms = (
        "support", "helpdesk", "customer service", "verify", "recovery",
        "security", "wallet", "official"
    )
    matched_terms = [term for term in impersonation_terms if term in searchable_text]
    brand_mentioned = any(
        normalized_name(reference) in normalized_name(searchable_text)
        for reference in reference_names if reference
    )
    if matched_terms and brand_mentioned:
        reasons.append("Brand-related impersonation wording: " + ", ".join(matched_terms))
        score += 20

    if candidate_type == "app" and brand_mentioned and developer:
        registered_developers = {
            normalized_name(application.get("developer", ""))
            for application in profile["applications"]
            if isinstance(application, dict) and application.get("developer")
        }
        if registered_developers and normalized_name(developer) not in registered_developers:
            reasons.append("App publisher does not match a registered official publisher")
            score += 35

    if not url.lower().startswith("https://"):
        reasons.append("Candidate URL does not use HTTPS")
        score += 10

    score = min(score, 100)
    if not reasons:
        reasons.append("No configured brand-impersonation indicators matched")
    status = "High Risk" if score >= 70 else "Medium Risk" if score >= 35 else "Low Risk"
    return score, status, reasons


def reconcile_official_candidates(conn, profile):
    rows = conn.execute("SELECT * FROM threats").fetchall()
    for row in rows:
        if is_registered_official(
            profile,
            row["candidate_type"],
            row["name"],
            row["url"],
            row["developer"]
        ):
            conn.execute("UPDATE threats SET is_flagged = 0 WHERE id = ?", (row["id"],))
        elif not row["is_flagged"]:
            score, status, reasons = assess_candidate(
                profile,
                row["candidate_type"],
                row["name"],
                row["url"],
                row["developer"],
                row["description"],
                bool(row["logo_match"])
            )
            conn.execute("""
                UPDATE threats
                SET risk_score = ?, status = ?, reasons = ?, is_flagged = ?
                WHERE id = ?
            """, (score, status, json.dumps(reasons), int(score >= 35), row["id"]))


def normalize_social_accounts(value):
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return []
    accounts = []
    for item in value:
        if isinstance(item, dict):
            platform = str(item.get("platform", "")).strip()
            url = str(item.get("url", "")).strip()
        else:
            platform, separator, url = str(item).partition("|")
            platform = platform.strip() if separator else ""
            url = url.strip() if separator else str(item).strip()
        if url:
            accounts.append({"platform": platform, "url": url})
    return accounts


def normalize_applications(value):
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return []
    applications = []
    for item in value:
        if isinstance(item, dict):
            application = {
                "name": str(item.get("name", "")).strip(),
                "developer": str(item.get("developer", "")).strip(),
                "url": str(item.get("url", "")).strip()
            }
        else:
            parts = [part.strip() for part in str(item).split("|", 2)]
            if len(parts) != 3:
                continue
            application = {"name": parts[0], "developer": parts[1], "url": parts[2]}
        if application["name"] and application["url"]:
            applications.append(application)
    return applications


def canonical_http_url(value):
    value = str(value or "").strip()
    if value and "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or "." not in parsed.hostname
        or any(char.isspace() for char in value)
    ):
        return None
    return value


def validate_image(file_storage):
    extension = os.path.splitext(secure_filename(file_storage.filename or ""))[1].lower()
    if extension not in ALLOWED_LOGO_EXTENSIONS:
        raise ValueError("Use a PNG, JPG, JPEG, or WebP image.")
    try:
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        image.verify()
        file_storage.stream.seek(0)
    except Exception as error:
        raise ValueError("The uploaded file is not a valid image.") from error
    return extension


def logos_are_similar(candidate_file, official_logo_path):
    official_filename = os.path.basename(urlparse(official_logo_path).path)
    official_path = os.path.join(UPLOAD_FOLDER, official_filename)
    if not official_filename or not os.path.isfile(official_path):
        return False
    candidate_file.stream.seek(0)
    candidate_hash = imagehash.phash(Image.open(candidate_file.stream).convert("RGB"))
    official_hash = imagehash.phash(Image.open(official_path).convert("RGB"))
    return candidate_hash - official_hash <= 10


def calculate_risk(url):
    parsed = urlparse(url)
    score = 0
    reasons = []

    if parsed.scheme != "https":
        score += 15
        reasons.append("URL does not use HTTPS")

    if len(url) > 75:
        score += 10
        reasons.append("URL is unusually long")

    suspicious_words = [
        "login", "verify", "support", "password",
        "account", "secure", "confirm"
    ]

    found = [
        word for word in suspicious_words
        if word in url.lower()
    ]

    if found:
        score += min(60, len(found) * 15)
        reasons.append(
            "Suspicious keywords: " + ", ".join(found)
        )

    score = min(score, 100)

    if score >= 70:
        status = "High Risk"
    elif score >= 35:
        status = "Medium Risk"
    else:
        status = "Low Risk"

    if not reasons:
        reasons.append("No obvious heuristic indicators detected")

    return score, status, reasons


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(app.root_path, "index.html")


@app.route("/style.css", methods=["GET"])
def stylesheet():
    return send_from_directory(app.root_path, "style.css")


@app.route("/script.js", methods=["GET"])
def frontend_script():
    return send_from_directory(app.root_path, "script.js")


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "message": "Flask backend is running"
    })


@app.route("/uploads/<path:filename>", methods=["GET"])
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/api/brand", methods=["GET", "PUT"])
def brand_profile():
    conn = connect_db()
    if request.method == "GET":
        profile = get_brand_profile(conn)
        conn.close()
        return jsonify(profile)

    data = request.get_json(silent=True) or {}
    brand_name = str(data.get("brand_name", "")).strip()
    if not brand_name:
        conn.close()
        return jsonify({"error": "Brand name is required"}), 400

    website = str(data.get("official_website", "")).strip()
    if website:
        website = canonical_http_url(website)
        if not website:
            conn.close()
            return jsonify({"error": "Enter a valid official website URL"}), 400

    accounts = normalize_social_accounts(data.get("social_accounts", []))
    applications = normalize_applications(data.get("applications", []))
    for account in accounts:
        account["url"] = canonical_http_url(account["url"])
        if not account["url"]:
            conn.close()
            return jsonify({"error": "An official social account URL is invalid"}), 400
    for application in applications:
        application["url"] = canonical_http_url(application["url"])
        if not application["url"]:
            conn.close()
            return jsonify({"error": "An official application URL is invalid"}), 400

    identifiers = clean_string_list(data.get("identifiers", []))
    conn.execute("""
        UPDATE brand_profile
        SET brand_name = ?, official_website = ?, social_accounts = ?,
            applications = ?, identifiers = ?, updated_at = ?
        WHERE id = 1
    """, (
        brand_name,
        website,
        json.dumps(accounts),
        json.dumps(applications),
        json.dumps(identifiers),
        datetime.now().isoformat()
    ))
    profile = get_brand_profile(conn)
    reconcile_official_candidates(conn, profile)
    conn.commit()
    conn.close()
    return jsonify(profile)


@app.route("/api/brand/logo", methods=["POST"])
def upload_brand_logo():
    logo = request.files.get("logo")
    if not logo or not logo.filename:
        return jsonify({"error": "Choose a logo image to upload"}), 400

    try:
        extension = validate_image(logo)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    filename = secure_filename(f"brand-{uuid.uuid4().hex}{extension}")
    logo.save(os.path.join(UPLOAD_FOLDER, filename))
    logo_path = f"/uploads/{filename}"

    conn = connect_db()
    previous_logo = conn.execute(
        "SELECT logo_path FROM brand_profile WHERE id = 1"
    ).fetchone()["logo_path"]
    conn.execute(
        "UPDATE brand_profile SET logo_path = ?, updated_at = ? WHERE id = 1",
        (logo_path, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    previous_filename = os.path.basename(urlparse(previous_logo).path)
    if previous_filename and previous_filename != filename:
        previous_path = os.path.join(UPLOAD_FOLDER, previous_filename)
        if os.path.isfile(previous_path):
            os.remove(previous_path)
    return jsonify({"logo_path": logo_path})


@app.route("/api/name-check", methods=["GET"])
def check_name():
    candidate_name = request.args.get("name", "").strip()
    if not candidate_name:
        return jsonify({"matches": []})

    conn = connect_db()
    profile = get_brand_profile(conn)
    conn.close()
    references = [profile["brand_name"]]
    references.extend(
        application.get("name", "")
        for application in profile["applications"]
        if isinstance(application, dict)
    )

    matches = []
    for reference in filter(None, references):
        if normalized_name(reference) == normalized_name(candidate_name):
            if reference.strip().lower() != candidate_name.lower():
                matches.append({
                    "official_name": reference,
                    "match_type": "Spacing or punctuation change"
                })
            continue
        variation = name_variation(reference, candidate_name)
        if not variation:
            variation = next((
                token_variation
                for token in candidate_name.split()
                if (token_variation := name_variation(reference, token))
            ), None)
        if variation:
            matches.append({"official_name": reference, "match_type": variation})

    return jsonify({"matches": matches})


@app.route("/api/threats", methods=["GET"])
def get_threats():
    conn = connect_db()

    rows = conn.execute(
        "SELECT * FROM threats WHERE is_flagged = 1 ORDER BY id DESC"
    ).fetchall()

    conn.close()
    threats = []
    for row in rows:
        threat = dict(row)
        try:
            threat["reasons"] = json.loads(threat["reasons"] or "[]")
        except json.JSONDecodeError:
            threat["reasons"] = [
                reason.strip() for reason in (threat["reasons"] or "").split(",")
                if reason.strip()
            ]
        threats.append(threat)
    return jsonify(threats)


@app.route("/api/monitor", methods=["POST"])
def monitor_candidate():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    candidate_type = str(data.get("candidate_type", "social")).strip().lower()
    if candidate_type not in {"social", "app", "website"}:
        return jsonify({"error": "Choose social, app, or website monitoring"}), 400

    name = str(data.get("name", "")).strip()
    url = canonical_http_url(data.get("url", ""))
    platform = str(data.get("platform", "")).strip()
    developer = str(data.get("developer", "")).strip()
    description = str(data.get("description", "")).strip()
    if not name or not url:
        return jsonify({"error": "Candidate name and a valid URL are required"}), 400
    if candidate_type == "app" and not platform:
        platform = "App store"
    if candidate_type == "social" and not platform:
        return jsonify({"error": "Select the social platform"}), 400
    if candidate_type == "website":
        platform = "Website"

    conn = connect_db()
    profile = get_brand_profile(conn)
    if not profile["brand_name"]:
        conn.close()
        return jsonify({"error": "Save a brand profile before monitoring candidates"}), 400

    if is_registered_official(profile, candidate_type, name, url, developer):
        conn.close()
        return jsonify({
            "excluded": True,
            "flagged": False,
            "status": "Official identity excluded",
            "reasons": ["Matches a registered official account or application"]
        }), 200

    logo_match = str(data.get("logo_match", "")).lower() in {"1", "true", "yes", "on"}
    candidate_logo = request.files.get("candidate_logo")
    if candidate_logo and candidate_logo.filename:
        if not profile["logo_path"]:
            conn.close()
            return jsonify({"error": "Upload the official logo before comparing candidate logos"}), 400
        try:
            validate_image(candidate_logo)
            logo_match = logos_are_similar(candidate_logo, profile["logo_path"])
        except (ValueError, OSError) as error:
            conn.close()
            return jsonify({"error": str(error) or "Could not compare the candidate logo"}), 400

    score, status, reasons = assess_candidate(
        profile, candidate_type, name, url, developer, description, logo_match
    )
    flagged = score >= 35
    cursor = conn.execute("""
        INSERT INTO threats
        (name, platform, url, risk_score, status, reasons, created_at,
         candidate_type, developer, description, logo_match, is_flagged)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        platform,
        url,
        score,
        status,
        json.dumps(reasons),
        datetime.now().isoformat(),
        candidate_type,
        developer,
        description,
        int(logo_match),
        int(flagged)
    ))
    candidate_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({
        "id": candidate_id,
        "name": name,
        "platform": platform,
        "candidate_type": candidate_type,
        "url": url,
        "developer": developer,
        "risk_score": score,
        "status": status,
        "reasons": reasons,
        "flagged": flagged,
        "excluded": False
    }), 201


@app.route("/api/scan-url", methods=["POST"])
def scan_url():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    platform = str(data.get("platform", "")).strip()
    url = str(data.get("url", "")).strip()

    if not name or not platform or not url:
        return jsonify({
            "error": "Name, platform and URL are required"
        }), 400

    if len(url) > 2048:
        return jsonify({"error": "URL is too long"}), 400

    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or "." not in parsed.hostname
        or any(char.isspace() for char in url)
    ):
        return jsonify({"error": "Please provide a valid URL"}), 400

    score, status, reasons = calculate_risk(url)

    conn = connect_db()

    cursor = conn.execute("""
        INSERT INTO threats
        (name, platform, url, risk_score, status, reasons, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        platform,
        url,
        score,
        status,
        ", ".join(reasons),
        datetime.now().isoformat()
    ))

    threat_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({
        "id": threat_id,
        "name": name,
        "platform": platform,
        "url": url,
        "risk_score": score,
        "status": status,
        "reasons": reasons,
        "message": "Heuristic scan completed"
    }), 201


@app.route("/api/dashboard/stats", methods=["GET"])
def dashboard_stats():
    conn = connect_db()

    total = conn.execute(
        "SELECT COUNT(*) FROM threats WHERE is_flagged = 1"
    ).fetchone()[0]

    high = conn.execute(
        "SELECT COUNT(*) FROM threats WHERE is_flagged = 1 AND risk_score >= 70"
    ).fetchone()[0]

    medium = conn.execute(
        "SELECT COUNT(*) FROM threats WHERE is_flagged = 1 AND risk_score >= 35 AND risk_score < 70"
    ).fetchone()[0]

    low = conn.execute(
        "SELECT COUNT(*) FROM threats WHERE is_flagged = 1 AND risk_score < 35"
    ).fetchone()[0]

    conn.close()

    return jsonify({
        "totalThreats": total,
        "highRisk": high,
        "mediumRisk": medium,
        "lowRisk": low
    })


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)