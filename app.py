
import os
import re
import json
import logging

import pandas as pd
import psycopg

from flask import (
    Flask,
    request,
    render_template,
    flash,
    redirect,
    url_for
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "lab-secret-key")

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def normalize_column_name(name):
    """Convert column names into lowercase snake_case."""

    name = str(name).strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_")


def process_file(file):

    filename = file.filename.lower()

    if filename.endswith(".csv"):
        df = pd.read_csv(file)

    elif filename.endswith(".xlsx"):
        df = pd.read_excel(file)

    else:
        raise ValueError("Only CSV and XLSX files are supported.")

    original_rows = len(df)
    original_columns = len(df.columns)

    # Normalize column names
    df.columns = [
        normalize_column_name(col)
        for col in df.columns
    ]

    # Remove whitespace from string values
    df = df.apply(
        lambda col: col.map(
            lambda value: value.strip()
            if isinstance(value, str)
            else value
        )
    )

    # Remove completely empty rows
    df = df.dropna(how="all")

    rows_after_empty_removal = len(df)

    # Remove duplicate rows
    df = df.drop_duplicates()

    cleaned_rows = len(df)

    # Calculate missing values
    missing_values = {
        str(column): int(count)
        for column, count in df.isna().sum().items()
    }

    report = {
        "original_rows": original_rows,
        "original_columns": original_columns,
        "rows_after_empty_removal": rows_after_empty_removal,
        "duplicate_rows_removed": (
            rows_after_empty_removal - cleaned_rows
        ),
        "cleaned_rows": cleaned_rows,
        "columns": list(df.columns),
        "missing_values": missing_values
    }

    # Convert DataFrame into JSON-compatible records
    records = json.loads(
        df.to_json(
            orient="records",
            date_format="iso"
        )
    )

    return df, report, records


def save_to_database(filename, report, records):

    if not DATABASE_URL:
        logger.warning(
            "DATABASE_URL not configured. "
            "Skipping database persistence."
        )
        return False

    with psycopg.connect(DATABASE_URL) as conn:

        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS upload_reports (
                    id BIGSERIAL PRIMARY KEY,
                    filename TEXT NOT NULL,
                    report JSONB NOT NULL,
                    cleaned_data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                INSERT INTO upload_reports
                (filename, report, cleaned_data)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (
                filename,
                json.dumps(report),
                json.dumps(records)
            ))

            report_id = cur.fetchone()[0]

    logger.info("Report saved with ID: %s", report_id)

    return report_id


def notify_manager(filename, report):

    manager_email = os.getenv(
        "MANAGER_EMAIL",
        "manager@example.com"
    )

    # Simulated notification for the first lab.
    logger.info(
        "EMAIL NOTIFICATION: To=%s | File=%s | Cleaned rows=%s",
        manager_email,
        filename,
        report["cleaned_rows"]
    )


@app.route("/", methods=["GET", "POST"])
def index():

    if request.method == "POST":

        if "file" not in request.files:
            flash("No file was uploaded.")
            return redirect(url_for("index"))

        uploaded_file = request.files["file"]

        if not uploaded_file.filename:
            flash("Please select a file.")
            return redirect(url_for("index"))

        try:

            df, report, records = process_file(uploaded_file)

            # Database persistence
            report_id = save_to_database(
                uploaded_file.filename,
                report,
                records
            )

            # Manager notification
            notify_manager(
                uploaded_file.filename,
                report
            )

            return render_template(
                "index.html",
                report=report,
                preview=df.head(10).to_html(
                    index=False,
                    classes="data-table",
                    border=0
                ),
                report_id=report_id
            )

        except Exception as error:

            logger.exception("File processing failed")
            flash(f"Processing failed: {error}")

            return redirect(url_for("index"))

    return render_template("index.html")


@app.route("/health")
def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)