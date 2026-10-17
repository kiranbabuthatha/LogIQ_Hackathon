import csv

from pathlib import Path

import psycopg

BGL_CSV_PATH = Path.home() / "Documents/loghub/BGL/BGL_2k.log_structured.csv"

DB_CONNECTION = (

    "host=localhost "

    "port=7890 "

    "dbname=postgres "

    "user=postgres"

)

def inspect_csv():

    with BGL_CSV_PATH.open("r", encoding="utf-8", newline="") as file:

        reader = csv.DictReader(file)

        print(f"CSV columns: {reader.fieldnames}")

        row_count = 0
        invalid_rows = 0

        for row in reader:

            row_count += 1

            if None in row or any(value is None for value in row.values()):
                invalid_rows += 1
                continue

            try:
                line_id = int(row["LineId"])
                timestamp = int(row["Timestamp"])

                if line_id <= 0 or timestamp <= 0:
                    invalid_rows += 1
                    continue
                
            except ValueError:
                invalid_rows += 1
                continue

            event_id = row["EventId"]

            if not event_id.startswith("E") or not event_id[1:].isdigit():
                invalid_rows += 1

        print(f"Total CSV records: {row_count}")
        print(f"Invalid records: {invalid_rows}")

        if invalid_rows > 0:
            raise ValueError(f"Found {invalid_rows} structurally invalid CSV records")

def load_csv_to_serenedb():

    columns = [

        "line_id",

        "label",

        "timestamp_raw",

        "date_raw",

        "node",

        "time_raw",

        "node_repeat",

        "type",

        "component",

        "level",

        "content",

        "event_id",

        "event_template",

    ]

    copy_query = (

        f"COPY bgl_logs_clean ({', '.join(columns)}) "

        "FROM STDIN"

    )

    with psycopg.connect(DB_CONNECTION) as conn:

        with conn.cursor() as cur:

            with BGL_CSV_PATH.open(

                "r", encoding="utf-8", newline=""

            ) as file:

                reader = csv.DictReader(file)

                with cur.copy(copy_query) as copy:

                    for row in reader:

                        values = (

                            int(row["LineId"]),

                            row["Label"],

                            int(row["Timestamp"]),

                            row["Date"],

                            row["Node"],

                            row["Time"],

                            row["NodeRepeat"],

                            row["Type"],

                            row["Component"],

                            row["Level"],

                            row["Content"],

                            row["EventId"],

                            row["EventTemplate"],

                        )

                        copy.write_row(values)

        conn.commit()

    print("BGL records imported successfully!")

def main():

    print("Starting BGL sample ingestion...")

    if not BGL_CSV_PATH.is_file():

        raise FileNotFoundError(

            f"BGL dataset not found: {BGL_CSV_PATH}"

        )

    print(f"Dataset found: {BGL_CSV_PATH}")

    inspect_csv()

    load_csv_to_serenedb()

if __name__ == "__main__":

    main()