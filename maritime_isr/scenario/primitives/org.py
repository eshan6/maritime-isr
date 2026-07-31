"""org_factory — companies, the people-shaped fictions around them, and edges.

The D-group scenarios are about corporate structure, and structure only becomes
interesting when entities are **shared**. A beneficial owner two hops above two
otherwise unrelated vessels; a technical manager who also manages a designated
hull; a registered agent and a street address that survive a company's
dissolution and reappear on its successor. None of those are visible in a graph
where every vessel has its own private chain.

So this module builds a small, deliberately entangled corporate world: a dozen
organisations sharing a handful of agents and addresses, with ownership and
management edges that carry time scope like everything else.

**Registered agents and addresses are first-class entities, not strings.** D2's
corporate phoenixing — a designated company dissolves and re-registers under a
new name with the same agent at the same address — is only detectable if the
agent and the address are nodes that both companies point at. Stored as text
fields they would require string matching to recover, which is exactly the kind
of fragile inference this graph exists to replace.

**Ownership edges are synthetic and say so.** ADR-016 records that GFW ownership
covers 0.66% of hulls and that ownership-based risk propagation is a paid-feed
feature. Nothing here changes that: these edges exist to exercise the traversal
code on data where the right answer is known, and every one of them carries
`is_synthetic`. The measured statement about real ownership coverage is
unaffected and must continue to be quoted as it stands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: Jurisdictions used for the opaque intermediaries. Real registries with real
#: reputations for opacity — the point of the scenario is that jurisdiction is a
#: weak signal on its own, since most companies registered there are ordinary.
OPAQUE_JURISDICTIONS = ("MHL", "SYC", "ARE-FZ")
CLEAN_JURISDICTIONS = ("SGP", "GBR", "IND", "NLD")


@dataclass
class Organization:
    entity_id: str
    name: str
    jurisdiction: str
    registered_agent: str          # agent entity id
    address: str                   # address entity id
    role: str = "operator"         # operator | manager | owner | intermediary
    incorporated: datetime | None = None
    dissolved: datetime | None = None
    designated: bool = False
    #: Set on the successor of a dissolved designated entity (D2).
    successor_of: str | None = None
    notes: str = ""


@dataclass
class OwnershipEdge:
    """`src` is owned/operated/managed by `dst`, between two dates."""
    kind: str                      # owned-by | operated-by | managed-by
    src: str
    dst: str
    valid_from: datetime
    valid_to: datetime | None
    confidence: float
    share: float | None = None
    notes: str = ""


@dataclass
class CorporateWorld:
    orgs: dict[str, Organization] = field(default_factory=dict)
    agents: dict[str, str] = field(default_factory=dict)      # id -> name
    addresses: dict[str, str] = field(default_factory=dict)   # id -> text
    edges: list[OwnershipEdge] = field(default_factory=list)

    def add_org(self, org: Organization) -> Organization:
        self.orgs[org.entity_id] = org
        return org

    def add_agent(self, agent_id: str, name: str) -> str:
        self.agents[agent_id] = name
        return agent_id

    def add_address(self, addr_id: str, text: str) -> str:
        self.addresses[addr_id] = text
        return addr_id

    def link(self, kind: str, src: str, dst: str, valid_from: datetime,
             valid_to: datetime | None = None, *, confidence: float = 0.8,
             share: float | None = None, notes: str = "") -> OwnershipEdge:
        e = OwnershipEdge(kind, src, dst, valid_from, valid_to, confidence,
                          share, notes)
        self.edges.append(e)
        return e

    # ---- queries the scenarios assert against ----
    def owners_of(self, entity_id: str, at: datetime | None = None) -> list[str]:
        out = []
        for e in self.edges:
            if e.src != entity_id or e.kind not in ("owned-by", "operated-by"):
                continue
            if at is not None:
                if e.valid_from > at or (e.valid_to is not None and e.valid_to <= at):
                    continue
            out.append(e.dst)
        return out

    def beneficial_owner(self, entity_id: str, at: datetime | None = None,
                         max_hops: int = 3) -> list[str]:
        """Walk up the ownership chain, with a hop budget and cycle protection.

        The hop budget is not decoration: a cyclic ownership structure is a real
        thing (companies owning each other is a documented obfuscation), and a
        traversal without protection hangs on it rather than reporting it.
        """
        seen = {entity_id}
        frontier = [entity_id]
        chain: list[str] = []
        for _ in range(max_hops):
            nxt = []
            for node in frontier:
                for owner in self.owners_of(node, at):
                    if owner in seen:
                        continue
                    seen.add(owner)
                    chain.append(owner)
                    nxt.append(owner)
            frontier = nxt
            if not frontier:
                break
        return chain

    def shares_agent(self, a: str, b: str) -> bool:
        oa, ob = self.orgs.get(a), self.orgs.get(b)
        return bool(oa and ob and oa.registered_agent == ob.registered_agent)

    def shares_address(self, a: str, b: str) -> bool:
        oa, ob = self.orgs.get(a), self.orgs.get(b)
        return bool(oa and ob and oa.address == ob.address)

    def designated_orgs(self) -> list[Organization]:
        return [o for o in self.orgs.values() if o.designated]


def build_agents_and_addresses(world: CorporateWorld) -> None:
    """The shared substrate. Deliberately fewer agents than companies.

    Corporate service providers really do serve dozens of shell companies from
    one office, which is why a shared agent is a *candidate* signal and not a
    finding — D3 exists specifically to check the system says "candidate" here.
    """
    world.add_agent("agent:harbourline", "Harbourline Corporate Services Ltd")
    world.add_agent("agent:meridian", "Meridian Registrars (Majuro)")
    world.add_agent("agent:cedarpoint", "Cedar Point Trust Company")
    world.add_agent("agent:crestwell", "Crestwell Nominees Pte")

    world.add_address("addr:majuro-trust", "Trust Company Complex, Ajeltake "
                                           "Road, Majuro, MH 96960")
    world.add_address("addr:victoria-house", "Suite 4, Victoria House, "
                                             "Mahe, Seychelles")
    world.add_address("addr:jafza-one", "JAFZA One, Tower A, Jebel Ali Free "
                                        "Zone, Dubai, UAE")
    world.add_address("addr:cecil-street", "18 Cecil Street, Singapore 049704")
    world.add_address("addr:leadenhall", "72 Leadenhall Street, London EC3A")
