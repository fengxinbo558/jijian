"""Versioned diagnostic knowledge cards and deterministic retrieval."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from .embeddings import LocalFeatureEmbeddingProvider

if TYPE_CHECKING:
    from .assets import AssetRegistry


REQUIRED_FIELDS = {
    "id",
    "version",
    "status",
    "domain",
    "title",
    "applies_to",
    "symptoms",
    "supporting_signals",
    "competing_causes",
    "counter_signals",
    "required_context",
    "verification_steps",
    "branch_conditions",
    "stop_conditions",
    "safe_actions",
    "prohibited_inferences",
    "sources",
    "review",
    "match",
}


class KnowledgeValidationError(ValueError):
    """Raised when the shipped knowledge pack violates its contract."""


def _strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _term_matches(term: str, text: str) -> bool:
    """Match short ASCII identifiers as words, not arbitrary substrings."""

    normalized = term.strip()
    if re.fullmatch(r"[A-Za-z0-9_]+", normalized):
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(normalized)}(?![A-Za-z0-9_])",
                text,
                flags=re.IGNORECASE,
            )
        )
    return normalized.lower() in text.lower()


class KnowledgeBase:
    """Load, validate, and retrieve a small auditable knowledge pack."""

    def __init__(
        self,
        path: Optional[str] = None,
        registry: Optional["AssetRegistry"] = None,
        embedding_provider: Optional[LocalFeatureEmbeddingProvider] = None,
    ) -> None:
        root = Path(__file__).resolve().parent.parent
        self.path = Path(path).expanduser().resolve() if path else root / "knowledge" / "diagnostic_cards.json"
        self.registry = registry
        self.embedding_provider = embedding_provider or LocalFeatureEmbeddingProvider()
        self.schema_version = ""
        self.sources: Dict[str, Dict[str, Any]] = {}
        self.cards: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.registry is not None:
            payload = self.registry.published_knowledge_payload()
        else:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise KnowledgeValidationError(f"knowledge pack not found: {self.path}") from exc
            except json.JSONDecodeError as exc:
                raise KnowledgeValidationError(f"knowledge pack is invalid JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise KnowledgeValidationError("knowledge pack root must be an object")
        self.schema_version = str(payload.get("schema_version", "")).strip()
        raw_sources = payload.get("sources")
        raw_cards = payload.get("cards")
        if not self.schema_version:
            raise KnowledgeValidationError("knowledge pack requires schema_version")
        if not isinstance(raw_sources, Mapping) or not isinstance(raw_cards, list):
            raise KnowledgeValidationError("knowledge pack requires sources and cards")
        self.sources = {
            str(key): dict(value)
            for key, value in raw_sources.items()
            if isinstance(value, Mapping)
        }
        self.cards = [self._validate_card(item) for item in raw_cards]
        identifiers = [card["id"] for card in self.cards]
        if len(identifiers) != len(set(identifiers)):
            raise KnowledgeValidationError("knowledge card ids must be unique")

    def _validate_card(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, Mapping):
            raise KnowledgeValidationError("each knowledge card must be an object")
        card = dict(value)
        missing = sorted(REQUIRED_FIELDS - set(card))
        if missing:
            raise KnowledgeValidationError(
                f"knowledge card {card.get('id', '<unknown>')} missing: {', '.join(missing)}"
            )
        for field in (
            "applies_to",
            "symptoms",
            "supporting_signals",
            "competing_causes",
            "counter_signals",
            "required_context",
            "verification_steps",
            "branch_conditions",
            "stop_conditions",
            "safe_actions",
            "prohibited_inferences",
            "sources",
        ):
            if not isinstance(card.get(field), list):
                raise KnowledgeValidationError(f"knowledge card {card['id']} field {field} must be a list")
        if not card["sources"]:
            raise KnowledgeValidationError(f"knowledge card {card['id']} requires a source")
        unknown_sources = [item for item in card["sources"] if item not in self.sources]
        if unknown_sources:
            raise KnowledgeValidationError(
                f"knowledge card {card['id']} has unknown sources: {unknown_sources}"
            )
        if not isinstance(card.get("review"), Mapping) or not card["review"].get("reviewed_at"):
            raise KnowledgeValidationError(f"knowledge card {card['id']} requires review metadata")
        if not isinstance(card.get("match"), Mapping):
            raise KnowledgeValidationError(f"knowledge card {card['id']} requires match metadata")
        return card

    def summary(self) -> Dict[str, Any]:
        domains: Dict[str, int] = {}
        for card in self.cards:
            domain = str(card["domain"])
            domains[domain] = domains.get(domain, 0) + 1
        return {
            "schema_version": self.schema_version,
            "card_count": len(self.cards),
            "domains": domains,
            "source_count": len(self.sources),
        }

    def get(self, card_id: str) -> Optional[Dict[str, Any]]:
        for card in self.cards:
            if card["id"] == card_id:
                return dict(card)
        return None

    @staticmethod
    def _terms(values: Iterable[Any]) -> Set[str]:
        return {str(value).strip().lower() for value in values if str(value).strip()}

    def search(
        self,
        *,
        rule_names: Sequence[str],
        fact_types: Sequence[str],
        text: str,
        device_type: str,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        """Return matched cards with transparent, deterministic reasons."""

        if self.registry is not None:
            self._load()

        rule_set = self._terms(rule_names)
        fact_set = self._terms(fact_types)
        lowered = text.lower()
        device = device_type.strip().lower()
        query_text = " ".join([text, device_type, *rule_names, *fact_types])
        query_vector = self.embedding_provider.embed([query_text])[0]
        found: List[Dict[str, Any]] = []
        for card in self.cards:
            match = card.get("match", {})
            matched_rules = sorted(rule_set & self._terms(match.get("rule_names", [])))
            matched_facts = sorted(fact_set & self._terms(match.get("fact_types", [])))
            matched_terms = [
                term for term in _strings(match.get("terms")) if _term_matches(term, lowered)
            ]
            applies = self._terms(card.get("applies_to", []))
            device_match = bool(device and device in applies)
            card_text = " ".join(
                str(value)
                for field in (
                    "title",
                    "applies_to",
                    "symptoms",
                    "supporting_signals",
                    "competing_causes",
                    "required_context",
                    "verification_steps",
                    "branch_conditions",
                )
                for value in (
                    card.get(field, []) if isinstance(card.get(field), list) else [card.get(field, "")]
                )
            )
            card_vector = self.embedding_provider.embed([card_text])[0]
            vector_similarity = self.embedding_provider.similarity(query_vector, card_vector)
            vector_only = not matched_facts and not matched_terms
            if vector_only and vector_similarity < 0.22:
                continue
            score = len(matched_rules) * 2 + len(matched_facts) * 5 + len(matched_terms) * 2
            reasons: List[str] = []
            if matched_rules:
                reasons.append("规则：" + "、".join(matched_rules))
            if matched_facts:
                reasons.append("事实：" + "、".join(matched_facts))
            if matched_terms:
                reasons.append("原文：" + "、".join(matched_terms[:4]))
            if device_match:
                reasons.append("设备类型适用")
                score += 1
            if vector_similarity >= 0.12:
                reasons.append(f"本地特征向量相似：{vector_similarity:.2f}")
                score += max(1, int(vector_similarity * 10))
            found.append(
                {
                    "card": dict(card),
                    "score": score,
                    "reasons": reasons,
                    "source_details": [self.sources[item] for item in card["sources"]],
                    "retrieval": {
                        "rules": matched_rules,
                        "facts": matched_facts,
                        "terms": matched_terms,
                        "device_match": device_match,
                        "vector_similarity": round(vector_similarity, 4),
                        "vector_provider": self.embedding_provider.provider_key,
                        "vector_capability": self.embedding_provider.capability,
                    },
                }
            )
        found.sort(key=lambda item: (-int(item["score"]), str(item["card"]["id"])))
        return found[: max(1, min(limit, 20))]
