import hashlib
import json
from datetime import date

import pytest

from fxlab.ecb_policy import decode_records, decision_links, effective_date, key_rates, normalize_decision, version_descriptor


HEADER = ["id", "pub_timestamp", "year", "issue_number", "type", "JEL_Code", "Taxonomy", "boardmember",
          "Authors", "documentTypes", "publicationProperties", "childrenPublication", "relatedPublications"]
PATH = "/press/pr/date/2024/html/ecb.mp240606~2148ecdb3c.en.html"
CHUNK = json.dumps([1, 1717676100, 2024, 0, 1, None, "Monetary policy", None, None, [PATH],
                    {"Title": "Monetary policy decisions"}, [], []]).encode()
OLD_ORDER = b'''<h1>Monetary policy decisions</h1> 6 June 2024. The interest rate on the main refinancing operations and the interest rates on the marginal lending facility and the deposit facility will be decreased to 4.25%, 4.50% and 3.75% respectively, with effect from 12 June 2024.'''
NEW_ORDER = "Monetary policy decisions. The interest rates on the deposit facility, the main refinancing operations and the marginal lending facility will remain unchanged at 2.00%, 2.15% and 2.40% respectively."


def meta(body):
    return {"sha256": hashlib.sha256(body).hexdigest(), "source_url": "https://www.ecb.europa.eu/test.html",
            "ingested_at": "2026-09-24T00:00:00+00:00"}


def test_foedb_chunk_and_decision_link():
    assert version_descriptor(b'[{"version":"1790244199","hash":"3fRmTtSs"}]')["hash"] == "3fRmTtSs"
    metadata = {"header": HEADER}
    assert decode_records(CHUNK, metadata)[0]["year"] == 2024
    assert decision_links([CHUNK], metadata, date(2024, 1, 1), date(2024, 12, 31)) == [{
        "decision_date": "2024-06-06", "published_at": "2024-06-06T14:15:00+02:00",
        "url": "https://www.ecb.europa.eu" + PATH,
    }]


def test_key_rate_orders_and_effective_date():
    assert key_rates(OLD_ORDER.decode()) == {"mro": 4.25, "marginal_lending": 4.5, "deposit": 3.75}
    assert key_rates(NEW_ORDER) == {"deposit": 2.0, "mro": 2.15, "marginal_lending": 2.4}
    assert effective_date(OLD_ORDER.decode()) == "2024-06-12"
    assert effective_date(NEW_ORDER) is None


def test_normalized_decision_keeps_availability_unknown():
    link = decision_links([CHUNK], {"header": HEADER}, date(2024, 1, 1), date(2024, 12, 31))[0]
    row = normalize_decision(OLD_ORDER, link, meta(OLD_ORDER))
    assert row["deposit_rate"] == 3.75 and row["mro_rate"] == 4.25
    assert row["published_time_quality"] == "exact_timestamp"
    assert row["available_at"] is None and row["strict_pit_eligible"] is False


def test_bad_chunk_and_timestamp_fail_closed():
    with pytest.raises(ValueError, match="chunk"):
        decode_records(b"[1, 2]", {"header": HEADER})
    link = {"decision_date": "2024-06-06", "published_at": "2024-06-06T13:15:00+02:00", "url": "x"}
    with pytest.raises(ValueError, match="14:15"):
        normalize_decision(OLD_ORDER, link, meta(OLD_ORDER))
