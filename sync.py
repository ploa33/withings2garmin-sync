#!/usr/bin/env python3
"""
Withings Scales (Body, Body+, Body Smart, Body Scan, Body Comp) → Garmin Connect daily sync.

Runs as a GitHub Actions workflow; persists Withings OAuth tokens and the
garth (Garmin SSO) session across runs to avoid token expiry and
Cloudflare anti-bot challenges.

Lookback window: last 30 days (syncs all weigh-ins chronologically).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import garminconnect

# ── Configuration ─────────────────────────────────────────────────────────────

WITHINGS_API      = "https://wbsapi.withings.net"
TOKENS_FILE       = Path("tokens.json")
GARTH_SESSION_DIR = Path("garth_session")
WEBHOOK_URL       = os.environ.get("WEBHOOK_URL", "").strip()
LOOKBACK_DAYS     = int(os.environ.get("LOOKBACK_DAYS", "30"))

# Withings measure type IDs → friendly names
MEASURE_TYPES = {
    1:  "weight",          # kg
    5:  "fat_free_mass",   # kg
    6:  "fat_percent",     # % (Withings already returns a percentage)
    8:  "fat_mass_weight", # kg
    76: "muscle_mass",     # kg
    77: "hydration",       # kg  → converted to % before Garmin upload
    88: "bone_mass",       # kg
}


def ensure_withings_webhook(access_token: str) -> None:
    """Ensure Withings Notify API is subscribed to the Google Apps Script proxy."""
    if not WEBHOOK_URL:
        return
    try:
        resp = requests.post(
            f"{WITHINGS_API}/notify",
            data={
                "action": "subscribe",
                "callbackurl": WEBHOOK_URL,
                "appli": 1,
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        data = resp.json()
        if data.get("status") in (0, 293):
            print("📡 Withings Realtime Webhook active and verified!")
        else:
            print(f"ℹ️ Withings webhook status: {data.get('status')} ({data.get('error', '')})")
    except Exception as exc:
        print(f"⚠️ Webhook subscription check skipped: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# Withings OAuth2
# ═══════════════════════════════════════════════════════════════════════════════

def load_withings_tokens() -> dict:
    """
    Load Withings tokens.
    Priority:
      1. tokens.json on disk (persisted by previous CI run).
      2. WITHINGS_REFRESH_TOKEN environment / GitHub secret (first run).
    """
    if TOKENS_FILE.exists():
        tokens = json.loads(TOKENS_FILE.read_text())
        if tokens.get("refresh_token"):
            print("📂 Loaded Withings tokens from tokens.json")
            return tokens

    refresh_token = os.environ.get("WITHINGS_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        print("❌ No Withings refresh token found. "
              "Set the WITHINGS_REFRESH_TOKEN GitHub secret.")
        sys.exit(1)

    print("🔑 Loaded Withings refresh token from environment secret")
    return {"refresh_token": refresh_token}


def refresh_withings_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """Exchange a refresh_token for a new access_token + refresh_token pair."""
    resp = requests.post(
        f"{WITHINGS_API}/v2/oauth2",
        data={
            "action":        "requesttoken",
            "grant_type":    "refresh_token",
            "client_id":     client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()

    if body.get("status") != 0:
        print(f"❌ Withings token refresh failed (status={body.get('status')}): "
              f"{body.get('error', body)}")
        sys.exit(1)

    print("🔄 Withings access token refreshed successfully")
    return body["body"]


def save_withings_tokens(token_data: dict) -> None:
    """Persist the refreshed Withings tokens to disk for the next run."""
    TOKENS_FILE.write_text(
        json.dumps(
            {
                "access_token":  token_data["access_token"],
                "refresh_token": token_data["refresh_token"],
                "userid":        token_data.get("userid", ""),
            },
            indent=2,
        )
    )
    print("💾 Withings tokens saved to tokens.json")


# ═══════════════════════════════════════════════════════════════════════════════
# Withings Measures
# ═══════════════════════════════════════════════════════════════════════════════

def decode_value(value: int, unit: int) -> float:
    """Decode a Withings raw measure: real_value = value × 10^unit."""
    return value * (10 ** unit)


def fetch_withings_measures(access_token: str, lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    """
    Fetch body-composition measurement groups from the last lookback_days days.
    """
    startdate = int(
        (datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp()
    )

    resp = requests.get(
        f"{WITHINGS_API}/measure",
        params={
            "action":     "getmeas",
            "startdate":  startdate,
            "category":   1,   # 1 = real measures (not goals)
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()

    if body.get("status") != 0:
        print(f"❌ Withings /measure API error (status={body.get('status')}): "
              f"{body.get('error', body)}")
        sys.exit(1)

    groups = body["body"].get("measuregrps", [])
    print(f"📊 Found {len(groups)} measurement group(s) in the last {lookback_days} days")
    return groups


def parse_measure_group(group: dict) -> dict:
    """Parse a Withings measure group dict into named float values."""
    result: dict[str, float] = {}
    for measure in group.get("measures", []):
        key = MEASURE_TYPES.get(measure["type"])
        if key:
            result[key] = round(decode_value(measure["value"], measure["unit"]), 4)
    return result


def group_measurements(groups: list[dict]) -> list[tuple[datetime, dict]]:
    """
    Cluster Withings measure groups into distinct weigh-in sessions.
    Groups within 300 seconds of each other are merged (e.g., weight + impedance).
    Returns list of (datetime, measures_dict) sorted chronologically (oldest first).
    """
    sorted_groups = sorted(groups, key=lambda g: g["date"])
    events: list[dict] = []

    for g in sorted_groups:
        parsed = parse_measure_group(g)
        if not parsed:
            continue
        g_date = g["date"]

        if events and abs(g_date - events[-1]["date"]) <= 300:
            for k, v in parsed.items():
                events[-1]["measures"][k] = v
            if "weight" in parsed:
                events[-1]["has_weight"] = True
        else:
            events.append({
                "date": g_date,
                "measures": parsed,
                "has_weight": "weight" in parsed,
            })

    valid_events: list[tuple[datetime, dict]] = []
    for ev in events:
        if ev["has_weight"] and ev["measures"].get("weight"):
            dt = datetime.fromtimestamp(ev["date"], tz=timezone.utc)
            valid_events.append((dt, ev["measures"]))

    return valid_events


# ═══════════════════════════════════════════════════════════════════════════════
# Garmin Connect
# ═══════════════════════════════════════════════════════════════════════════════

def get_garmin_client(email: str, password: str) -> garminconnect.Garmin:
    """
    Return an authenticated Garmin client using tokenstore caching.
    """
    garmin = garminconnect.Garmin(email, password)
    GARTH_SESSION_DIR.mkdir(exist_ok=True)
    garmin.login(tokenstore=str(GARTH_SESSION_DIR))
    print(f"✅ Garmin login successful (session stored in {GARTH_SESSION_DIR})")
    return garmin


def upload_to_garmin(
    garmin: garminconnect.Garmin,
    measure_dt: datetime,
    measures: dict,
) -> None:
    """Upload body composition data to Garmin Connect."""
    weight         = measures.get("weight")
    fat_percent    = measures.get("fat_percent")
    fat_mass_kg    = measures.get("fat_mass_weight")
    fat_free_kg    = measures.get("fat_free_mass")
    hydration_kg   = measures.get("hydration")
    bone_mass_kg   = measures.get("bone_mass")
    muscle_mass_kg = measures.get("muscle_mass")

    # Fallback calculation for fat % if not returned directly by type 6
    if fat_percent is None and weight and weight > 0:
        if fat_mass_kg is not None:
            fat_percent = round((fat_mass_kg / weight) * 100, 2)
        elif fat_free_kg is not None:
            fat_percent = round(((weight - fat_free_kg) / weight) * 100, 2)

    # Withings returns hydration in kg; Garmin expects a percentage
    hydration_percent: float | None = None
    if hydration_kg is not None and weight and weight > 0:
        hydration_percent = round((hydration_kg / weight) * 100, 2)

    timestamp_str = measure_dt.isoformat()

    print(f"\n  📤 Syncing weigh-in: {measure_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"     Weight      : {weight} kg")
    print(f"     Fat         : {fat_percent} %" if fat_percent is not None else "     Fat         : --")
    print(f"     Muscle mass : {muscle_mass_kg} kg" if muscle_mass_kg is not None else "     Muscle mass : --")
    print(f"     Hydration   : {hydration_percent} %" if hydration_percent is not None else "     Hydration   : --")
    print(f"     Bone mass   : {bone_mass_kg} kg" if bone_mass_kg is not None else "     Bone mass   : --")

    garmin.add_body_composition(
        timestamp=timestamp_str,
        weight=weight,
        percent_fat=fat_percent,
        percent_hydration=hydration_percent,
        bone_mass=bone_mass_kg,
        muscle_mass=muscle_mass_kg,
    )

    print("     ✅ Synced to Garmin Connect!")


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 60)
    print("  Withings Body+ → Garmin Connect Sync")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    # ── Read environment / secrets ────────────────────────────────────────────
    garmin_email    = os.environ.get("GARMIN_EMAIL", "").strip()
    garmin_password = os.environ.get("GARMIN_PASSWORD", "").strip()
    client_id       = os.environ.get("WITHINGS_CLIENT_ID", "").strip()
    client_secret   = os.environ.get("WITHINGS_CLIENT_SECRET", "").strip()

    missing = [
        name for name, val in {
            "GARMIN_EMAIL":           garmin_email,
            "GARMIN_PASSWORD":        garmin_password,
            "WITHINGS_CLIENT_ID":     client_id,
            "WITHINGS_CLIENT_SECRET": client_secret,
        }.items()
        if not val
    ]
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    # ── Step 1 — Withings OAuth2 ──────────────────────────────────────────────
    print("\n── Step 1 / 4 : Withings OAuth2 token refresh ──")
    tokens     = load_withings_tokens()
    token_data = refresh_withings_token(client_id, client_secret,
                                        tokens["refresh_token"])
    save_withings_tokens(token_data)
    ensure_withings_webhook(token_data["access_token"])

    # ── Step 2 — Fetch measures ───────────────────────────────────────────────
    is_simulation = "--simulate" in sys.argv or os.environ.get("SIMULATE", "").lower() == "true"
    
    # Check custom lookback argument e.g. --days 60
    lookback = LOOKBACK_DAYS
    for i, arg in enumerate(sys.argv):
        if arg in ("--days", "-d") and i + 1 < len(sys.argv):
            try:
                lookback = int(sys.argv[i + 1])
            except ValueError:
                pass

    if is_simulation:
        print("\n🧪 SIMULATION MODE ACTIVATED — Injecting fake weight & body composition data")
        weigh_ins = [
            (
                datetime.now(timezone.utc),
                {
                    "weight": 74.5,
                    "fat_percent": 17.8,
                    "muscle_mass": 36.1,
                    "hydration": 41.0,
                    "bone_mass": 3.2,
                }
            )
        ]
    else:
        print(f"\n── Step 2 / 4 : Fetch Withings measures (last {lookback} days) ──")
        groups = fetch_withings_measures(token_data["access_token"], lookback_days=lookback)

        if not groups:
            print(f"ℹ️  No measurements found in the last {lookback} days.")
            print("   Nothing to sync — exiting cleanly.")
            sys.exit(0)

        weigh_ins = group_measurements(groups)
        print(f"📈 Found {len(weigh_ins)} distinct weigh-in session(s) to sync.")

        if not weigh_ins:
            print("ℹ️  No valid weigh-in with weight found — nothing to sync.")
            sys.exit(0)

    # ── Step 3 — Garmin authentication ───────────────────────────────────────
    print("\n── Step 3 / 4 : Garmin Connect authentication ──")
    garmin = get_garmin_client(garmin_email, garmin_password)

    # ── Step 4 — Upload all weigh-ins ─────────────────────────────────────────
    print(f"\n── Step 4 / 4 : Upload {len(weigh_ins)} weigh-in(s) to Garmin Connect ──")
    for measure_dt, measures in weigh_ins:
        upload_to_garmin(garmin, measure_dt, measures)

    print("\n🎉 Sync complete for all measurements.\n")


if __name__ == "__main__":
    main()
