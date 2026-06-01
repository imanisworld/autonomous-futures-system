"""Read-only broker capability registry.

Inspired by terminal-style multi-broker platforms, but intentionally safer:
this registry describes available broker lanes and routing eligibility. It does
not create broker clients, open sockets, place orders, or read credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from config.settings import SystemConfig, load_config


@dataclass(frozen=True)
class BrokerDescriptor:
    key: str
    label: str
    asset_classes: tuple[str, ...]
    account_modes: tuple[str, ...]
    supports_brackets: bool
    supports_options: bool
    supports_futures: bool
    implementation_status: str  # active | stub | dormant | planned
    default_enabled: bool
    execution_route_allowed: bool
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "asset_classes": list(self.asset_classes),
            "account_modes": list(self.account_modes),
            "supports_brackets": self.supports_brackets,
            "supports_options": self.supports_options,
            "supports_futures": self.supports_futures,
            "implementation_status": self.implementation_status,
            "default_enabled": self.default_enabled,
            "execution_route_allowed": self.execution_route_allowed,
            "notes": list(self.notes),
        }


def broker_registry(config: Optional[SystemConfig] = None) -> list[BrokerDescriptor]:
    cfg = config or load_config()
    priority = list(getattr(cfg, "broker_priority", []) or [])

    descriptors = {
        "paper": BrokerDescriptor(
            key="paper",
            label="PaperBroker",
            asset_classes=("futures",),
            account_modes=("paper",),
            supports_brackets=True,
            supports_options=False,
            supports_futures=True,
            implementation_status="active",
            default_enabled=True,
            execution_route_allowed=True,
            notes=(
                "Default Railway path.",
                "No broker credentials, no network, simulated fills only.",
            ),
        ),
        "tradovate_sim": BrokerDescriptor(
            key="tradovate_sim",
            label="Tradovate Sim",
            asset_classes=("futures",),
            account_modes=("sim",),
            supports_brackets=True,
            supports_options=False,
            supports_futures=True,
            implementation_status="stub",
            default_enabled=False,
            execution_route_allowed=False,
            notes=(
                "Primary futures broker target after paper validation.",
                "Not routed until explicit live/sim promotion work is complete.",
            ),
        ),
        "alpaca_options": BrokerDescriptor(
            key="alpaca_options",
            label="Alpaca Options",
            asset_classes=("options", "equities"),
            account_modes=("paper", "live"),
            supports_brackets=False,
            supports_options=True,
            supports_futures=False,
            implementation_status="dormant",
            default_enabled=False,
            execution_route_allowed=False,
            notes=(
                "Isolated options lane; disabled by default.",
                "Uses options-specific risk rules, not futures tick/point logic.",
                "No futures webhook routing.",
            ),
        ),
        "ibkr_paper": BrokerDescriptor(
            key="ibkr_paper",
            label="IBKR Paper",
            asset_classes=("futures", "options", "equities"),
            account_modes=("paper",),
            supports_brackets=True,
            supports_options=True,
            supports_futures=True,
            implementation_status="dormant",
            default_enabled=False,
            execution_route_allowed=False,
            notes=(
                "IB Gateway/TWS adapter exists for paper futures experiments.",
                "Not part of Railway PaperBroker route.",
            ),
        ),
    }

    ordered: list[BrokerDescriptor] = []
    seen: set[str] = set()
    for key in priority:
        if key in descriptors and key not in seen:
            ordered.append(descriptors[key])
            seen.add(key)
    for key, descriptor in descriptors.items():
        if key not in seen:
            ordered.append(descriptor)
    return ordered


def get_broker_descriptor(key: str, config: Optional[SystemConfig] = None) -> BrokerDescriptor | None:
    normalized = key.strip().lower()
    for descriptor in broker_registry(config):
        if descriptor.key == normalized:
            return descriptor
    return None


def routable_brokers(config: Optional[SystemConfig] = None) -> list[BrokerDescriptor]:
    return [broker for broker in broker_registry(config) if broker.execution_route_allowed]


def broker_matrix(config: Optional[SystemConfig] = None) -> dict:
    brokers = broker_registry(config)
    return {
        "brokers": [broker.to_dict() for broker in brokers],
        "routable": [broker.key for broker in brokers if broker.execution_route_allowed],
        "active_default": next((broker.key for broker in brokers if broker.default_enabled), None),
    }


def supports_asset_class(asset_class: str, config: Optional[SystemConfig] = None) -> list[BrokerDescriptor]:
    wanted = asset_class.strip().lower()
    return [
        broker for broker in broker_registry(config)
        if wanted in {item.lower() for item in broker.asset_classes}
    ]
