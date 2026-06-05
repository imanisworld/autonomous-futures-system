from __future__ import annotations

from types import SimpleNamespace

from sources.public_client import PublicQuoteClient


def test_public_quote_client_missing_credentials_fails_soft():
    quote = PublicQuoteClient(api_secret_key="", default_account_number="").fetch_equity_quote("AAPL")

    assert quote.ok is False
    assert quote.error == "credentials_missing"


def test_public_quote_client_formats_sdk_quote(monkeypatch):
    class FakeInstrumentType:
        EQUITY = "EQUITY"

    class FakeOrderInstrument:
        def __init__(self, symbol, type):
            self.symbol = symbol
            self.type = type

    monkeypatch.setattr(
        "public_api_sdk.InstrumentType",
        FakeInstrumentType,
        raising=False,
    )
    monkeypatch.setattr(
        "public_api_sdk.OrderInstrument",
        FakeOrderInstrument,
        raising=False,
    )

    class FakeSdk:
        def get_quotes(self, instruments):
            assert instruments[0].symbol == "AAPL"
            assert instruments[0].type == "EQUITY"
            return [SimpleNamespace(last=201.5, bid=201.4, ask=201.6, volume=1234)]

    quote = PublicQuoteClient(
        api_secret_key="secret",
        default_account_number="account",
        sdk_client=FakeSdk(),
    ).fetch_equity_quote("AAPL")

    assert quote.ok is True
    assert quote.last == 201.5
    assert quote.bid == 201.4
    assert quote.ask == 201.6
    assert quote.volume == 1234
