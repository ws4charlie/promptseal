"""Tests for promptseal.erc8004.

Spec (BRIEF §10, RESUMPTION-NOTES §"4 specific gotchas"):
- build_agent_card() emits BRIEF §10 schema verbatim (name/description/endpoints/publicKey/version).
- agent_card_to_data_uri() canonicalizes (sorted keys, compact) before base64,
  so the byte stream is reproducible across languages and signing layers.
- register_agent() decodes the minted tokenId from the ERC-721 Transfer log's
  third indexed topic — NOT from the function's nominal return value, which
  EVM does not surface in tx receipts (RESUMPTION-NOTES gotcha #1).
- register_agent() raises ERC8004RegistrationError when the receipt is missing
  a Transfer log (defensive: wrong contract / failed mint / spec drift).
- get_agent_card() reads tokenURI(uint256) and returns the stored URI string.

Tests mock web3 — no live RPC. Live verification happens in scripts/01_*.
"""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

from eth_abi import encode as abi_encode

from promptseal.canonical import canonical_json
from promptseal.erc8004 import (
    AGENT_CARD_EVENT_TOPIC,
    ERC8004RegistrationError,
    TRANSFER_EVENT_TOPIC,
    agent_card_to_data_uri,
    build_agent_card,
    get_agent_card_from_register_tx,
    register_agent,
)


# -- agent card builder ------------------------------------------------------

def test_build_agent_card_matches_brief_schema():
    card = build_agent_card(
        public_key_b64="ZmFrZV9wdWJsaWNfa2V5XzMyX2J5dGVzX3Jhd19lZDI1NTE5X2tleQ==",
        agent_id="hr-screener-v1",
    )
    assert card == {
        "name": "hr-screener-v1",
        "description": "PromptSeal demo: hiring agent screening senior full-stack engineers",
        "endpoints": {"http": "https://example.com/agent"},
        "publicKey": "ed25519:ZmFrZV9wdWJsaWNfa2V5XzMyX2J5dGVzX3Jhd19lZDI1NTE5X2tleQ==",
        "version": "0.1",
    }


def test_build_agent_card_version_override():
    card = build_agent_card(
        public_key_b64="QUJD",
        agent_id="hr-screener-v1",
        version="0.2",
    )
    assert card["version"] == "0.2"


# -- data URI encoding -------------------------------------------------------

def test_agent_card_data_uri_is_canonical_and_decodable():
    card = build_agent_card(public_key_b64="QUJD", agent_id="hr-screener-v1")
    uri = agent_card_to_data_uri(card)

    assert uri.startswith("data:application/json;base64,")
    payload_b64 = uri.removeprefix("data:application/json;base64,")

    decoded = base64.b64decode(payload_b64)
    # Round-trip equals canonical bytes of the same dict — no whitespace,
    # sorted keys, UTF-8 (BRIEF §13 pitfall #1).
    assert decoded == canonical_json(card)
    assert json.loads(decoded) == card


def test_agent_card_data_uri_is_deterministic():
    """Same input dict → identical URI byte-for-byte across calls."""
    card = build_agent_card(public_key_b64="QUJD", agent_id="hr-screener-v1")
    assert agent_card_to_data_uri(card) == agent_card_to_data_uri(card)


# -- register_agent: tokenId extraction --------------------------------------

def _mock_w3_with_register(
    *,
    transfer_topic: bytes = TRANSFER_EVENT_TOPIC,
    token_id: int = 398,
    extra_logs: list | None = None,
    estimate_gas: int = 200_000,
    base_fee: int = 1_000_000,
    chain_id: int = 84532,
    status: int = 1,
    block_number: int = 41_094_999,
    tx_hash_hex: str = "ab" * 32,
):
    """Build a MagicMock web3 client that simulates a successful register() TX.

    Returned mock has the surface register_agent uses: contract().functions.register(),
    eth.estimate_gas, eth.get_block, eth.get_transaction_count, eth.send_raw_transaction,
    eth.wait_for_transaction_receipt, eth.chain_id.
    """
    w3 = MagicMock()
    w3.is_connected.return_value = True
    w3.eth.chain_id = chain_id

    # Gas / fee surface
    w3.eth.estimate_gas.return_value = estimate_gas
    w3.eth.get_block.return_value = {"baseFeePerGas": base_fee}
    w3.eth.get_transaction_count.return_value = 7
    w3.to_wei = MagicMock(side_effect=lambda v, unit: int(v) * 10**9)
    w3.keccak = MagicMock(
        side_effect=lambda text=None, **kw: TRANSFER_EVENT_TOPIC
        if text == "Transfer(address,address,uint256)"
        else b"\x00" * 32
    )

    # Mint receipt — one Transfer log with token_id in topics[3].
    # Real web3 returns HexBytes (a bytes subclass) for log topics; raw bytes
    # exercises the same isinstance(..., bytes) path in _decode_token_id_from_logs.
    transfer_log = MagicMock()
    transfer_log.topics = [
        transfer_topic,
        b"\x00" * 32,                              # from = 0x0
        b"\x00" * 12 + b"\xaa" * 20,               # to (irrelevant)
        token_id.to_bytes(32, "big"),              # tokenId
    ]

    logs = [transfer_log] + (extra_logs or [])

    receipt = MagicMock()
    receipt.status = status
    receipt.blockNumber = block_number
    receipt.gasUsed = 180_000
    receipt.logs = logs

    w3.eth.send_raw_transaction.return_value = bytes.fromhex(tx_hash_hex)
    w3.eth.wait_for_transaction_receipt.return_value = receipt

    # Contract surface — register() builds a tx dict; estimate_gas and
    # build_transaction return shaped dicts the function will sign+send.
    contract = MagicMock()
    register_fn = MagicMock()
    register_fn.estimate_gas.return_value = estimate_gas
    register_fn.build_transaction.return_value = {
        "type": 2,
        "chainId": chain_id,
        "from": "0x0cA7BFe2E76D950e71F4A2E9AC6D071D5379eC56",
        "to": "0x7177a6867296406881E20d6647232314736Dd09A",
        "value": 0,
        "nonce": 7,
        "data": b"\xab\xcd",
        "gas": estimate_gas,
        "maxFeePerGas": base_fee * 2 + 10**9,
        "maxPriorityFeePerGas": 10**9,
    }
    contract.functions.register.return_value = register_fn
    w3.eth.contract.return_value = contract

    return w3, contract, register_fn, receipt


def test_register_agent_decodes_token_id_from_transfer_log(monkeypatch):
    w3, contract, register_fn, receipt = _mock_w3_with_register(token_id=398)

    # Minimal Account stub so we don't need a real key.
    fake_account = MagicMock()
    fake_account.address = "0x0cA7BFe2E76D950e71F4A2E9AC6D071D5379eC56"
    signed = MagicMock()
    signed.raw_transaction = b"\xde\xad\xbe\xef"
    fake_account.sign_transaction.return_value = signed

    result = register_agent(
        card_uri="data:application/json;base64,QUJD",
        w3=w3,
        account=fake_account,
        registry_address="0x7177a6867296406881E20d6647232314736Dd09A",
    )

    assert result["token_id"] == 398
    assert result["tx_hash"].startswith("0x")
    assert len(result["tx_hash"]) == 66
    assert result["block_number"] == 41_094_999
    # Confirm the contract function was invoked with the URI we passed in.
    contract.functions.register.assert_called_once_with(
        "data:application/json;base64,QUJD"
    )


def test_register_agent_applies_30pct_gas_buffer():
    w3, contract, register_fn, _ = _mock_w3_with_register(estimate_gas=200_000)
    fake_account = MagicMock()
    fake_account.address = "0x0cA7BFe2E76D950e71F4A2E9AC6D071D5379eC56"
    signed = MagicMock()
    signed.raw_transaction = b"\xde\xad"
    fake_account.sign_transaction.return_value = signed

    register_agent(
        card_uri="data:application/json;base64,QUJD",
        w3=w3,
        account=fake_account,
        registry_address="0x7177a6867296406881E20d6647232314736Dd09A",
    )

    # The tx that gets signed must reflect 200_000 * 1.3 = 260_000.
    sent_tx = fake_account.sign_transaction.call_args.args[0]
    assert sent_tx["gas"] == 260_000


def test_register_agent_no_transfer_log_raises():
    """RESUMPTION-NOTES gotcha #1: defensive — a register that does not mint."""
    # Replace the would-be Transfer log with an unrelated topic.
    bogus_topic = b"\xff" * 32
    w3, *_ = _mock_w3_with_register(transfer_topic=bogus_topic)
    fake_account = MagicMock()
    fake_account.address = "0x0cA7BFe2E76D950e71F4A2E9AC6D071D5379eC56"
    fake_account.sign_transaction.return_value = MagicMock(raw_transaction=b"\x00")

    with pytest.raises(ERC8004RegistrationError, match="Transfer"):
        register_agent(
            card_uri="data:application/json;base64,QUJD",
            w3=w3,
            account=fake_account,
            registry_address="0x7177a6867296406881E20d6647232314736Dd09A",
        )


def test_register_agent_reverted_tx_raises():
    w3, *_ = _mock_w3_with_register(status=0)
    fake_account = MagicMock()
    fake_account.address = "0x0cA7BFe2E76D950e71F4A2E9AC6D071D5379eC56"
    fake_account.sign_transaction.return_value = MagicMock(raw_transaction=b"\x00")

    with pytest.raises(ERC8004RegistrationError, match="reverted"):
        register_agent(
            card_uri="data:application/json;base64,QUJD",
            w3=w3,
            account=fake_account,
            registry_address="0x7177a6867296406881E20d6647232314736Dd09A",
        )


def test_register_agent_estimate_gas_failure_raises():
    """Gotcha #3: estimate_gas revert means a semantic failure — fail loud,
    don't silently fall back to a higher hardcoded ceiling."""
    w3, contract, register_fn, _ = _mock_w3_with_register()
    register_fn.estimate_gas.side_effect = Exception(
        "execution reverted: AlreadyRegistered"
    )

    fake_account = MagicMock()
    fake_account.address = "0x0cA7BFe2E76D950e71F4A2E9AC6D071D5379eC56"
    fake_account.sign_transaction.return_value = MagicMock(raw_transaction=b"\x00")

    with pytest.raises(ERC8004RegistrationError, match="estimate_gas"):
        register_agent(
            card_uri="data:application/json;base64,QUJD",
            w3=w3,
            account=fake_account,
            registry_address="0x7177a6867296406881E20d6647232314736Dd09A",
        )


# -- get_agent_card_from_register_tx ----------------------------------------

def test_get_agent_card_from_register_tx_decodes_uri_from_event_log():
    """Recover the submitted URI from the registry's custom event in log data.

    Mirrors what we observed on Base Sepolia for token 633: the URI does NOT
    live at tokenURI(uint256); it sits ABI-encoded as a string in the data
    field of a log whose topic[0] == AGENT_CARD_EVENT_TOPIC.
    """
    w3 = MagicMock()
    expected_uri = "data:application/json;base64,QUJD"
    encoded_data = abi_encode(["string"], [expected_uri])

    transfer_log = MagicMock()
    transfer_log.topics = [TRANSFER_EVENT_TOPIC, b"\x00" * 32, b"\x00" * 32, b"\x00" * 32]
    transfer_log.data = b""

    card_log = MagicMock()
    card_log.topics = [
        AGENT_CARD_EVENT_TOPIC,
        (633).to_bytes(32, "big"),
        b"\x00" * 12 + b"\xaa" * 20,
    ]
    card_log.data = encoded_data

    receipt = MagicMock()
    receipt.logs = [transfer_log, card_log]
    w3.eth.get_transaction_receipt.return_value = receipt

    out = get_agent_card_from_register_tx(w3, "0xabcd")

    assert out == expected_uri
    w3.eth.get_transaction_receipt.assert_called_once_with("0xabcd")


def test_get_agent_card_from_register_tx_no_event_raises():
    """Defensive: if the registry didn't emit the agent-card event, fail loud."""
    w3 = MagicMock()
    transfer_log = MagicMock()
    transfer_log.topics = [TRANSFER_EVENT_TOPIC, b"\x00" * 32, b"\x00" * 32, b"\x00" * 32]
    transfer_log.data = b""
    receipt = MagicMock()
    receipt.logs = [transfer_log]
    w3.eth.get_transaction_receipt.return_value = receipt

    with pytest.raises(ERC8004RegistrationError, match="agent-card event"):
        get_agent_card_from_register_tx(w3, "0xabcd")
