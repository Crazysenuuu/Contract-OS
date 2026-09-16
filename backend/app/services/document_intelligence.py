"""Document intelligence service for clause extraction, smart tagging, and clause library."""
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func, select

from app.models.document_intelligence import (
    ExtractedClause, ClauseLibrary, SmartTag, ClauseSimilarity,
    ClauseCategory, ClauseRiskLevel, ClauseSentiment
)
from app.models.agreement import Agreement


class DocumentIntelligenceService:
    """Service for AI-powered document intelligence."""

    # Category keyword patterns for auto-classification
    CATEGORY_PATTERNS = {
        ClauseCategory.CONFIDENTIALITY: [
            r"confidential(?:ity)?", r"non-disclosure", r"proprietary",
            r"trade secret", r"保密", r"non.?disclosure"
        ],
        ClauseCategory.INDEMNIFICATION: [
            r"indemnif", r"hold harmless", r"defend and hold"
        ],
        ClauseCategory.LIABILITY: [
            r"liability", r"limitation of liability", r"damages",
            r"aggregate liability", r"consequential damages"
        ],
        ClauseCategory.TERMINATION: [
            r"terminat", r"expir", r"cancell", r"rescind"
        ],
        ClauseCategory.GOVERNING_LAW: [
            r"governing law", r"applicable law", r"jurisdiction",
            r"laws of the state", r"laws of the republic"
        ],
        ClauseCategory.DISPUTE_RESOLUTION: [
            r"dispute resolution", r"arbitrat", r"mediat",
            r"litigation", r"court of competent"
        ],
        ClauseCategory.IP_OWNERSHIP: [
            r"intellectual property", r"ownership", r"work.?for.?hire",
            r"assignment of intellectual"
        ],
        ClauseCategory.IP_LICENSE: [
            r"license", r"grant of license", r"right to use",
            r"licens(?:e|ing)"
        ],
        ClauseCategory.NON_COMPETE: [
            r"non.?compete", r"compete", r"restrictive covenant"
        ],
        ClauseCategory.NON_SOLICITATION: [
            r"non.?solicit", r"solicit", r"recruit"
        ],
        ClauseCategory.REPRESENTATIONS: [
            r"representations? and warranties", r"represents? and warrants"
        ],
        ClauseCategory.WARRANTIES: [
            r"warrant(?:y|ies)", r"disclaim", r"as.?is"
        ],
        ClauseCategory.FORCE_MAJEURE: [
            r"force majeure", r"act of god", r"unforeseeable"
        ],
        ClauseCategory.SEVERABILITY: [
            r"severability", r"severable", r"invalid provision"
        ],
        ClauseCategory.ENTIRE_AGREEMENT: [
            r"entire agreement", r"integration", r"supersedes"
        ],
        ClauseCategory.PAYMENT: [
            r"payment", r"fee", r"compensation", r"invoic"
        ],
        ClauseCategory.INSURANCE: [
            r"insurance", r"insur(?:ance|e|ing)", r"policy"
        ],
        ClauseCategory.DATA_PROTECTION: [
            r"data protection", r"privacy", r"gdpr", r"personal data"
        ],
        ClauseCategory.AUDIT_RIGHTS: [
            r"audit", r"inspection", r"right to audit"
        ],
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    # ===== CLAUSE EXTRACTION =====

    async def extract_clauses(
        self,
        agreement_id: str,
        text: str,
        version_id: Optional[str] = None
    ) -> List[Clause]:
        """Extract and classify clauses from agreement text."""
        # Simple section-based extraction
        sections = self._split_into_sections(text)
        clauses = []

        for i, (title, content, start_pos) in enumerate(sections):
            if len(content.strip()) < 20:  # Skip very short sections
                continue

            # Classify the clause
            category = self._classify_clause(content)
            risk_level = self._assess_risk(content, category)
            sentiment = self._analyze_sentiment(content)
            entities = self._extract_entities(content)
            tags = self._generate_tags(content, category)

            clause = ExtractedClause(
                agreement_id=agreement_id,
                version_id=version_id,
                text=content.strip(),
                title=title,
                section_number=self._extract_section_number(title),
                category=category,
                tags=tags,
                risk_level=risk_level[0],
                risk_score=risk_level[1],
                sentiment=sentiment[0],
                sentiment_score=sentiment[1],
                key_entities=entities,
                start_char=start_pos,
                end_char=start_pos + len(content),
            )
            self.db.add(clause)
            clauses.append(clause)

        await self.db.commit()

        # Auto-tag the agreement
        await self._apply_smart_tags(agreement_id, clauses)

        return clauses

    def _split_into_sections(self, text: str) -> List[Tuple[str, str, int]]:
        """Split text into sections based on common patterns."""
        sections = []

        # Match numbered sections (1., 1.1, Article 1, Section 1, etc.)
        patterns = [
            r'(?:^|\n)((?:Article|Section|Clause|§)\s+\d+(?:\.\d+)*(?:\s*[:.]\s*).+?)(?=\n(?:Article|Section|Clause|§)\s+\d+|\Z)',
            r'(?:^|\n)(\d+(?:\.\d+)*\.?\s+.+?)(?=\n\d+(?:\.\d+)*\.?\s+|\Z)',
            r'(?:^|\n)((?:ARTICLE|SECTION|CLAUSE)\s+\w+[:.]\s*.+?)(?=\n(?:ARTICLE|SECTION|CLAUSE)\s+\w+[:.]|\Z)',
        ]

        for pattern in patterns:
            matches = list(re.finditer(pattern, text, re.MULTILINE | re.DOTALL))
            if len(matches) >= 3:  # Good section detection
                for match in matches:
                    title_end = match.group(1).find('\n')
                    if title_end == -1:
                        title = match.group(1).strip()
                        content = ""
                    else:
                        title = match.group(1)[:title_end].strip()
                        content = match.group(1)[title_end:]
                    sections.append((title, content, match.start()))
                break

        # Fallback: split by double newlines
        if not sections:
            parts = text.split('\n\n')
            pos = 0
            for i, part in enumerate(parts):
                if len(part.strip()) > 50:
                    title = part[:min(80, len(part))].strip()
                    if '\n' in title:
                        title = title.split('\n')[0]
                    sections.append((f"Section {i+1}", part, pos))
                pos += len(part) + 2

        return sections

    def _classify_clause(self, text: str) -> ClauseCategory:
        """Classify a clause based on its content."""
        text_lower = text.lower()
        scores = {}

        for category, patterns in self.CATEGORY_PATTERNS.items():
            score = 0
            for pattern in patterns:
                matches = len(re.findall(pattern, text_lower))
                score += matches
            if score > 0:
                scores[category] = score

        if scores:
            return max(scores, key=scores.get)
        return ClauseCategory.OTHER

    def _assess_risk(self, text: str, category: ClauseCategory) -> Tuple[ClauseRiskLevel, float]:
        """Assess the risk level of a clause."""
        text_lower = text.lower()
        risk_score = 0.3  # Base score

        # High-risk indicators
        high_risk_patterns = [
            r"unlimited liability", r"no limitation", r"personal guarantee",
            r"indemnif.*all losses", r"sole discretion", r"irrevocable",
            r"perpetual", r"non.?terminable", r"waive.*all rights"
        ]

        medium_risk_patterns = [
            r"reasonable", r"best efforts", r"commercially reasonable",
            r"may terminate", r"sole discretion", r"solely determined"
        ]

        for pattern in high_risk_patterns:
            if re.search(pattern, text_lower):
                risk_score += 0.15

        for pattern in medium_risk_patterns:
            if re.search(pattern, text_lower):
                risk_score += 0.05

        # Category-specific risks
        if category == ClauseCategory.LIABILITY:
            if "unlimited" in text_lower:
                risk_score += 0.2
            if "consequential" in text_lower and "exclude" not in text_lower:
                risk_score += 0.1

        if category == ClauseCategory.CONFIDENTIALITY:
            if "indefinite" in text_lower or "perpetual" in text_lower:
                risk_score += 0.1
            if len(text) < 100:  # Very short confidentiality clause
                risk_score += 0.1

        risk_score = min(1.0, risk_score)

        if risk_score >= 0.7:
            return ClauseRiskLevel.CRITICAL, risk_score
        elif risk_score >= 0.5:
            return ClauseRiskLevel.HIGH, risk_score
        elif risk_score >= 0.3:
            return ClauseRiskLevel.MEDIUM, risk_score
        else:
            return ClauseRiskLevel.LOW, risk_score

    def _analyze_sentiment(self, text: str) -> Tuple[ClauseSentiment, float]:
        """Analyze the sentiment/favorability of a clause."""
        text_lower = text.lower()

        favorable_indicators = [
            r"mutual", r"reciprocal", r"both parties", r"equitable",
            r"reasonable", r"fair", r"balanced"
        ]

        unfavorable_indicators = [
            r"sole discretion", r"unilateral", r"may terminate.*without",
            r"no liability", r"no obligation", r"as.?is",
            r"no warranty", r"at risk"
        ]

        favorable_score = sum(1 for p in favorable_indicators if re.search(p, text_lower))
        unfavorable_score = sum(1 for p in unfavorable_indicators if re.search(p, text_lower))

        total = favorable_score + unfavorable_score
        if total == 0:
            return ClauseSentiment.NEUTRAL, 0.0

        score = (favorable_score - unfavorable_score) / total

        if score > 0.2:
            return ClauseSentiment.FAVORABLE, score
        elif score < -0.2:
            return ClauseSentiment.UNFAVORABLE, score
        else:
            return ClauseSentiment.NEUTRAL, score

    def _extract_entities(self, text: str) -> Dict:
        """Extract key entities from clause text."""
        entities = {
            "monetary_values": [],
            "durations": [],
            "dates": [],
            "obligations": [],
        }

        # Monetary values
        money_pattern = r'\$[\d,]+(?:\.\d{2})?(?:\s*(?:million|billion|M|B))?'
        entities["monetary_values"] = re.findall(money_pattern, text, re.IGNORECASE)

        # Durations
        duration_pattern = r'\b\d+\s*(?:day|week|month|year|s)\b(?:\s*(?:or|and)\s*\d+\s*(?:day|week|month|year|s)\b)*'
        entities["durations"] = re.findall(duration_pattern, text, re.IGNORECASE)

        # Obligation keywords
        obligation_patterns = [
            r"shall\s+\w+(?:\s+\w+){0,5}",
            r"must\s+\w+(?:\s+\w+){0,5}",
            r"required to\s+\w+(?:\s+\w+){0,5}",
            r"obligated to\s+\w+(?:\s+\w+){0,5}",
        ]
        for pattern in obligation_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            entities["obligations"].extend(matches[:3])  # Limit

        return entities

    def _generate_tags(self, text: str, category: ClauseCategory) -> List[str]:
        """Generate smart tags for a clause."""
        tags = []
        text_lower = text.lower()

        # Category tag
        tags.append(category.value)

        # Scope tags
        if "mutual" in text_lower:
            tags.append("mutual")
        elif "one-way" in text_lower or "unilateral" in text_lower:
            tags.append("one-way")

        # Duration tags
        if "perpetual" in text_lower or "indefinite" in text_lower:
            tags.append("indefinite-duration")
        elif re.search(r'\d+\s*year', text_lower):
            tags.append("fixed-term")

        # Limitation tags
        if "limit" in text_lower:
            tags.append("limited")
        if "unlimited" in text_lower:
            tags.append("unlimited")

        # Exclusion tags
        if "exclud" in text_lower:
            tags.append("has-exclusions")
        if "exception" in text_lower:
            tags.append("has-exceptions")

        return tags

    def _extract_section_number(self, title: str) -> Optional[str]:
        """Extract section number from title."""
        match = re.match(r'^(?:Article|Section|Clause|§)?\s*(\d+(?:\.\d+)*)', title)
        return match.group(1) if match else None

    async def _apply_smart_tags(self, agreement_id: str, clauses: List[Clause]):
        """Apply smart tags based on clause analysis."""
        tags_result = await self.db.execute(
            select(SmartTag).where(SmartTag.is_system == True)  # noqa: E712
        )
        tags = tags_result.scalars().all()

        # Auto-apply system tags
        for clause in clauses:
            for tag in tags:
                should_apply = False

                # Check keyword matches
                if tag.keywords:
                    text_lower = clause.text.lower()
                    if any(kw.lower() in text_lower for kw in tag.keywords):
                        should_apply = True

                # Check category matches
                if tag.categories and clause.category.value in tag.categories:
                    should_apply = True

                # Check risk score
                if tag.min_risk_score and clause.risk_score and clause.risk_score >= tag.min_risk_score:
                    should_apply = True

                if should_apply:
                    if tag.name not in (clause.tags or []):
                        clause.tags = (clause.tags or []) + [tag.name]
                    tag.usage_count += 1
                    tag.last_used_at = datetime.utcnow()

        await self.db.commit()

    # ===== CLAUSE LIBRARY =====

    async def get_clause_suggestions(
        self,
        category: ClauseCategory,
        jurisdiction: Optional[str] = None,
        agreement_type: Optional[str] = None,
        limit: int = 10
    ) -> List[ClauseLibrary]:
        """Get clause suggestions from the library."""
        query = (
            select(ClauseLibrary)
            .where(
                ClauseLibrary.category == category,
                ClauseLibrary.is_approved == True,  # noqa: E712
            )
        )

        if jurisdiction:
            query = query.where(ClauseLibrary.jurisdictions.contains([jurisdiction]))

        if agreement_type:
            query = query.where(ClauseLibrary.agreement_types.contains([agreement_type]))

        result = await self.db.execute(
            query.order_by(ClauseLibrary.usage_count.desc()).limit(limit)
        )
        return result.scalars().all()

    async def add_to_library(
        self,
        clause_id: str,
        organization_id: str,
        title: str,
        description: Optional[str] = None,
        jurisdictions: List[str] = None,
        agreement_types: List[str] = None,
        created_by: str = None
    ) -> ClauseLibrary:
        """Add an extracted clause to the clause library."""
        clause_result = await self.db.execute(
            select(ExtractedClause).where(ExtractedClause.id == clause_id)
        )
        clause = clause_result.scalar_one_or_none()
        if not clause:
            raise ValueError("Clause not found")

        library_entry = ClauseLibrary(
            organization_id=organization_id,
            title=title,
            text=clause.text,
            description=description,
            category=clause.category,
            tags=clause.tags,
            jurisdictions=jurisdictions or [],
            agreement_types=agreement_types or [],
            risk_level=clause.risk_level,
            risk_score=clause.risk_score,
            created_by=created_by,
        )
        self.db.add(library_entry)
        await self.db.commit()
        await self.db.refresh(library_entry)
        return library_entry

    async def find_similar_clauses(
        self,
        clause_id: str,
        threshold: float = 0.7,
        limit: int = 5
    ) -> List[Tuple[ClauseLibrary, float]]:
        """Find similar clauses in the library."""
        clause_result = await self.db.execute(
            select(ExtractedClause).where(ExtractedClause.id == clause_id)
        )
        clause = clause_result.scalar_one_or_none()
        if not clause:
            return []

        # Simple text similarity (in production, use embeddings)
        lib_result = await self.db.execute(
            select(ClauseLibrary).where(ClauseLibrary.category == clause.category)
        )
        library_clauses = lib_result.scalars().all()

        similar = []
        clause_words = set(clause.text.lower().split())

        for lib_clause in library_clauses:
            lib_words = set(lib_clause.text.lower().split())
            if not clause_words or not lib_words:
                continue

            # Jaccard similarity
            intersection = clause_words & lib_words
            union = clause_words | lib_words
            similarity = len(intersection) / len(union) if union else 0

            if similarity >= threshold:
                similar.append((lib_clause, similarity))

        similar.sort(key=lambda x: x[1], reverse=True)
        return similar[:limit]

    async def get_clause_stats(self, organization_id: str) -> Dict:
        """Get clause statistics for an organization."""
        total_clauses = (await self.db.execute(
            select(func.count(ExtractedClause.id))
        )).scalar_one()
        total_library = (await self.db.execute(
            select(func.count(ClauseLibrary.id)).where(
                ClauseLibrary.organization_id == organization_id
            )
        )).scalar_one()

        # Risk distribution
        risk_dist = dict(
            (await self.db.execute(
                select(ExtractedClause.risk_level, func.count(ExtractedClause.id))
                .group_by(ExtractedClause.risk_level)
            )).all()
        )

        # Category distribution
        cat_dist = dict(
            (await self.db.execute(
                select(ExtractedClause.category, func.count(ExtractedClause.id))
                .group_by(ExtractedClause.category)
            )).all()
        )

        return {
            "total_clauses_extracted": total_clauses,
            "library_entries": total_library,
            "risk_distribution": {k.value: v for k, v in risk_dist.items() if k},
            "category_distribution": {k.value: v for k, v in cat_dist.items() if k},
        }
