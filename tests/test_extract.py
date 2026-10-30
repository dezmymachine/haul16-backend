from engine.extract import extract, unofficial


def test_ghana_phone_formats():
    text = "Call 024 000 0101, +233 55 000 0202, 233200000303 or 030-200-0000. Ref 12024000010199."
    assert extract(text).phones == ["0240000101", "0550000202", "0200000303", "0302000000"]


def test_wallets_and_links():
    text = "USDT TRC20: TNkwaExDemoFakeWa11etDoNotUse12345 ETH 0x" + "ab" * 20
    ids = extract(text, ["https://wa.me/233240000101", "https://t.me/nkwa_support_gh"])
    assert ids.wallets["tron"] == ["TNkwaExDemoFakeWa11etDoNotUse12345"]
    assert ids.wallets["ethereum"] == ["0x" + "ab" * 20]
    assert ids.telegram == ["nkwa_support_gh"]
    assert "0240000101" in ids.phones          # wa.me number becomes a phone


def test_unofficial_filters_register():
    ids = extract("Support 030 200 0000, agent 024 000 0101")
    assert unofficial(ids, ["0302000000"], []) == {"phones": ["0240000101"], "wallets": []}
