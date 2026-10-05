# Ganesh Festival Dashboard

This is a separate Ganesh Festival dashboard backed by a Google Sheet. Admins can add records through the website; viewers can only view the dashboard. The dashboard fetches the sheet as CSV and refreshes automatically every 5 seconds.

## Google Sheet format

Use one worksheet with this header row:

```text
Date | Type | Name | Committee Member Name | Amount Asked By | Category | Amount | Payment Method | Description | Phone Number | Committee Member Amount | Receipt URL
```

The connector also supports the older `Donation Amount` and `Spent Amount` headers if they are still present in the sheet.

Use these values in `Type`:

- `Donation` for general donations
- `Expense` for festival expenses
- `Volunteer Donation` for donations made by volunteers

Example rows:

```text
2026-10-01 | Donation | Ravi Kumar | General | 0 | UPI | Opening day donation | 9876543210 | 5000
2026-10-01 | Expense |  | Decoration | 1800 | Cash | Flowers and lights |  | 0
2026-10-01 | Volunteer Donation | Suresh | Volunteer | 0 | UPI | Volunteer contribution | 9123456789 | 2500
```

The donor dashboard combines rows with the same phone number into one unique donor and adds their donation amounts. Phone numbers are masked in the dashboard except for the last four digits.

## Committee members tab

Create a second tab named `committee-members` with exactly these columns:

```text
Committee Member Name | Fixed Amount | Phone Number
```

The dashboard displays the listed members, their fixed amount, paid committee-member amount from the main sheet, and remaining amount. The target count is configured as 22 with `COMMITTEE_MEMBER_COUNT=22`.

## Login

The default local credentials are:

```text
Admin:  admin / admin123
Viewer: viewer / viewer123
```

Set your own values in `.env` before deploying to Render. Admins see the entry form. Viewers do not see it and cannot call the write endpoint.

## Enable admin writes to Google Sheets

The public CSV export is read-only, so admin submissions use the included `google-apps-script/Code.gs` connector:

1. Open the festival spreadsheet and choose **Extensions → Apps Script**.
2. Paste the contents of `google-apps-script/Code.gs`.
3. Replace `WRITE_TOKEN` with a long random value and save.
4. Choose **Deploy → New deployment → Web app**.
5. Set **Execute as** to yourself and **Who has access** to **Anyone**.
6. Copy the web-app URL into `GOOGLE_SHEETS_WRITE_URL` in `.env` or Render environment variables.
7. Set `GOOGLE_SHEETS_WRITE_TOKEN` to the same token used in `Code.gs`.

The Apps Script maps the website form to the existing headers (`Date`, `Type`, `Name`, `Committee Member Name`, `Amount Asked By`, `Category`, `Amount`, `Payment Method`, `Description`, `Phone Number`, `Committee Member Amount`, and `Receipt URL`) and appends a new row. For an uploaded receipt, it creates a view-only Google Drive file, makes the link available to viewers, and stores that link in `Receipt URL`. The dashboard then reads the updated sheet automatically.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn backend.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`.

The Google Sheet must be shared as **Anyone with the link → Viewer** for the CSV export approach. The website never embeds the Google Sheet. Only the admin form can request a new row through the protected server endpoint.

## Render

Deploy as a **Web Service**, not a Static Site:

```text
Build command: pip install -r requirements.txt
Start command: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

The included `render.yaml` contains this configuration.
