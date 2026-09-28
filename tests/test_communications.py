import json
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from openpyxl import Workbook

from fxlab.communications import build_communication_targets, parse_ea_ced
from fxlab.macro import write_macro_parquet


def workbook_bytes():
    book = Workbook()
    book.remove(book.active)
    events = book.create_sheet("1. IMC Events (unfiltered)")
    events.append(["ID", "Date", "hour", "minute", "t_account", "s_executiveboard", "s_president",
                   "s_ECBpreshearing", "s_BdF", "s_Buba", "s_BdI", "s_BdE", "interview",
                   "t_speechinECBDB", "ECBDB_ID", "ECBDB_ID2", "ECBDB_ID3", "ECBDB_ID4"])
    events.append([7, date(2020, 1, 15), 14, 30, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 42, None, None, None])
    returns = book.create_sheet("2. IMC All returns (filtered)")
    returns.append(["ID", "Date", "Hour", "Minute", "EUROSTOXX", "OIS_2Y", "OIS_10Y", "EURUSD"])
    returns.append([7, "15-Jan-2020", 14, 30, 0.2, 1.5, 2.0, 0.25])
    abnormal = book.create_sheet("3. IMC Abnormal returns (filt) ")
    abnormal.append(["ID", "EURUSD"])
    abnormal.append([7, 0.25])
    speeches = book.create_sheet("6. ECB speeches database")
    speeches.append(["date", "speakers", "title", "subtitle", "contents", "ECBDBID"])
    speeches.append([date(2020, 1, 15), "Speaker", "Title", "Subtitle", "Full speech text", 42])
    target = BytesIO()
    book.save(target)
    return target.getvalue()


def test_parse_ea_ced_links_text_and_market_reaction():
    events, examples, summary = parse_ea_ced(workbook_bytes(), date(2020, 1, 1), date(2020, 12, 31))
    assert len(events) == len(examples) == 1
    assert events[0]["event_type"] == "ecb_president_communication"
    assert events[0]["event_time"].isoformat() == "2020-01-15T13:30:00+00:00"
    assert events[0]["eurusd_return_bp"] == 25.0
    assert events[0]["eurusd_abnormal"] is True
    assert examples[0]["text"] == "Full speech text"
    assert examples[0]["strict_pit_eligible"] is False
    assert summary["median_absolute_eurusd_return_pct"] == 0.25


def test_communication_targets_store_outcomes_without_model_predictions(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    start = datetime(2020, 1, 1, 13, tzinfo=timezone.utc)
    events = [{"event_id": "event-before-market", "event_time": start - timedelta(days=1), "event_type": "speech",
               "eurusd_return_pct": 0.2, "eurusd_abnormal": False, "speaker": "Old", "title": "Old", "full_text": "Old"},
              {"event_id": "event-1", "event_time": start + timedelta(hours=1), "event_type": "speech",
               "eurusd_return_pct": 0.1, "eurusd_abnormal": False, "speaker": "Speaker",
               "title": "Title", "full_text": "Text"}]
    bars = [{"session_date": date(2020, 1, 1) + timedelta(days=index),
             "bar_end": start + timedelta(days=index, hours=8), "close": 1 + index / 100,
             "complete": True} for index in range(62)]
    write_macro_parquet(events, tmp_path/"silver/events.parquet", ("event_time",))
    write_macro_parquet(bars, tmp_path/"silver/bars.parquet", ("bar_end",))
    (tmp_path/"reports").mkdir()
    (tmp_path/"reports/communications.json").write_text(json.dumps({
        "dataset_id": "communications-1", "files": {"events": "silver/events.parquet"}}))
    (tmp_path/"reports/bars.json").write_text(json.dumps({
        "dataset_id": "bars-1", "files": {"d1": "silver/bars.parquet"}}))
    report = build_communication_targets()
    assert report["rows"] == report["rows_with_text"] == 1
    assert report["coverage"]["60d"]["rows"] == 1
    assert report["track_policy"]["combined_score"] is False
