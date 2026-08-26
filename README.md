# ⚖️ Withings ➔ Garmin Connect

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-orange.svg?style=flat&logo=buy-me-a-coffee)](https://buymeacoffee.com/ploa33)

> **⚠️ IMPORTANT**: Use the green **`Use this template`** button to create a **PRIVATE** repository. Do not make it public (session tokens are saved in the repo).

Sync weigh-ins and body metrics from any **Withings Scale** (Body, Body+, Smart, Scan, Comp) to **Garmin Connect** in real-time (< 30s) — 100% Cloud, 0€, 0 server.

---

## 🚀 Setup

### 1. Enable Workflow Permissions
In your new private repository:
* Go to **Settings ➔ Actions ➔ General ➔ Workflow permissions**.
* Select **Read and write permissions** and click **Save**.

### 2. GitHub Secrets
In **Settings ➔ Secrets and variables ➔ Actions**, add:

| Secret | Description |
|---|---|
| `GARMIN_EMAIL` | Garmin Connect email |
| `GARMIN_PASSWORD` | Garmin Connect password |
| `WITHINGS_CLIENT_ID` | From [Withings Developer Portal](https://developer.withings.com/) (Callback: `https://example.com`) |
| `WITHINGS_CLIENT_SECRET` | From Withings Developer Portal |
| `WITHINGS_REFRESH_TOKEN` | Initial OAuth token (*see below*) |
| `WEBHOOK_URL` | *(Optional)* Google Apps Script URL for instant sync |

<details>
<summary><b>🔑 How to get your WITHINGS_REFRESH_TOKEN</b></summary>

1. Open this URL in your browser (replace `YOUR_CLIENT_ID`):
   ```text
   https://account.withings.com/oauth2_user/authorize2?response_type=code&client_id=YOUR_CLIENT_ID&state=init&scope=user.metrics,user.info&redirect_uri=https://example.com
   ```
2. Log in, authorize, and copy the `code=` from the redirected URL address bar.
3. Run this command in a terminal (fill in your values):
   ```bash
   curl --request POST 'https://wbsapi.withings.net/v2/oauth2' \
     --data-urlencode 'action=requesttoken' \
     --data-urlencode 'grant_type=authorization_code' \
     --data-urlencode 'client_id=YOUR_CLIENT_ID' \
     --data-urlencode 'client_secret=YOUR_CLIENT_SECRET' \
     --data-urlencode 'code=YOUR_AUTHORIZATION_CODE' \
     --data-urlencode 'redirect_uri=https://example.com'
   ```
4. Copy the `refresh_token` from the JSON response and add it as a secret.
</details>

---

## ⚡ Real-Time Sync *(Optional)*

<details>
<summary><b>Google Apps Script Webhook Setup (< 30s sync)</b></summary>

1. Create a project on [Google Apps Script](https://script.google.com/) and paste:
   ```javascript
   function doPost(e) {
     UrlFetchApp.fetch("https://api.github.com/repos/YOUR_GITHUB_USER/withings2garmin-sync/dispatches", {
       "method": "post",
       "headers": {
         "Authorization": "Bearer ghp_YOUR_GITHUB_PAT",
         "Accept": "application/vnd.github.v3+json"
       },
       "payload": JSON.stringify({"event_type": "withings_sync"}),
       "muteHttpExceptions": true
     });
     return ContentService.createTextOutput("OK");
   }
   function doGet(e) { return ContentService.createTextOutput("Active"); }
   ```
2. **Deploy** ➔ **New deployment** ➔ **Web app** (Execute as: *Me*, Who has access: *Anyone*).
3. Copy the URL into your `WEBHOOK_URL` GitHub Secret.
</details>

---

## 🧪 Testing

* **GitHub Actions**: Go to **Actions** ➔ **Run workflow** ➔ Check `🧪 Simulate a fake weigh-in`.
* **Local**: `python sync.py --simulate`

---

## 📊 Metrics

Syncs Weight, Body Fat %, Muscle Mass, Hydration %, and Bone Mass with a 7-day automatic catch-up.
Fallback safety cron runs twice daily (09:00 & 22:00 CEST).

---

## ☕ Support

If this project saved you the cost of a Garmin Index scale or made your life easier, consider buying me a coffee!

<a href="https://buymeacoffee.com/ploa33" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="45"></a>

---

## 📄 License
[MIT](LICENSE)
