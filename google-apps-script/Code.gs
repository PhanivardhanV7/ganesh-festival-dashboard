// Paste this into Extensions -> Apps Script in the festival spreadsheet.
// Set WRITE_TOKEN to the same value as GOOGLE_SHEETS_WRITE_TOKEN on the server.
const WRITE_TOKEN = 'f3e590713ae6739199b29cd1dc896c1d188a5575403021a343afe582b4ac52fa';

function normalize(value) {
  return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, ' ');
}

function json(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet() {
  return json({ ok: true, service: 'Ganesh Festival Sheets connector' });
}

function doPost(event) {
  try {
    const body = JSON.parse(event.postData.contents || '{}');
    if (WRITE_TOKEN && body.token !== WRITE_TOKEN) {
      return json({ ok: false, error: 'Unauthorized write request.' });
    }

    const rowData = body.row || {};
    if (body.receipt && body.receipt.base64) {
      const base64 = String(body.receipt.base64).replace(/^data:[^;]+;base64,/, '');
      const bytes = Utilities.base64Decode(base64);
      const blob = Utilities.newBlob(bytes, body.receipt.mime_type || 'image/jpeg', body.receipt.name || 'expense-receipt');
      const file = DriveApp.createFile(blob);
      file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
      rowData['receipt url'] = file.getUrl();
    }
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    const lastColumn = sheet.getLastColumn();
    if (!lastColumn) return json({ ok: false, error: 'Add the header row to the sheet first.' });

    const headers = sheet.getRange(1, 1, 1, lastColumn).getValues()[0];
    const values = headers.map(function(header) {
      const key = normalize(header);
      if (key === 'committee member amount') {
        return rowData['committee member amount'] || rowData['donation amount'] || rowData['spent amount'] || rowData.amount || '';
      }
      if (key === 'spent amount') {
        return rowData['spent amount'] || rowData['committee member amount'] || rowData['donation amount'] || rowData.amount || '';
      }
      if (key === 'donation amount') {
        return rowData['donation amount'] || rowData['committee member amount'] || rowData['spent amount'] || '';
      }
      return rowData[key] === undefined ? '' : rowData[key];
    });
    // Write the complete row in one range operation. This is faster and more
    // reliable for web-app requests than appendRow on larger sheets.
    const nextRow = Math.max(sheet.getLastRow() + 1, 2);
    sheet.getRange(nextRow, 1, 1, values.length).setValues([values]);
    return json({ ok: true });
  } catch (error) {
    return json({ ok: false, error: String(error) });
  }
}
