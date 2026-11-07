#!/usr/bin/env python3
"""
Kalkulator FinOps i progu rentowności dla s3-dedup-engine.

Model porównuje dwa światy:
  * BASELINE  - każdy plik trafia bezpośrednio do Amazon S3 (1 x PutObject, pełny storage),
  * NARZĘDZIE - brama Go + AI Worker + indeks wektorowy + DynamoDB + S3 (wywołania wg kodu).

Uwzględnia:
  * wszystkie płatne wywołania S3 (PutObject, HeadObject, DeleteObject) i DynamoDB
    (Query, GetItem, PutItem, DeleteItem) wynikające z kodu bramy,
  * stałe przechowywanie rekordów FILE#<nazwa>/META oraz CHUNK#<sha256>/FULL#<sha256>
    w DynamoDB (tabela nigdy nie jest wyłączana) i obiektów metadata/<plik>.json w S3,
  * cztery warianty indeksu wektorowego: lokalny PostgreSQL + pgvector, Amazon S3 Vectors,
    Amazon Aurora PostgreSQL Serverless v2 (pgvector), Amazon RDS for PostgreSQL (pgvector),
  * dwa reżimy: A - zbiór wgrany raz, potem nic nie przybywa (S3 + DynamoDB stoją),
                B - ciągły miesięczny napływ plików.

Cennik: AWS us-east-1, stan zweryfikowany 2026-10-25 (źródła w RAPORT_FINOPS.md).
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, replace

PRICING_DATE = "2026-10-25"
GIB = 1024.0**3
TIB = 1024.0**4
HORIZONS = (1, 3, 6, 12, 24, 36)
BACKENDS = ("local", "s3vectors", "aurora_pause", "aurora_min", "rds_micro")
BACKEND_NAMES = {
    "local": "PostgreSQL + pgvector lokalnie",
    "s3vectors": "Amazon S3 Vectors",
    "aurora_pause": "Aurora Serverless v2, auto-pause (min 0 ACU)",
    "aurora_min": "Aurora Serverless v2, min 0,5 ACU",
    "rds_micro": "RDS for PostgreSQL db.t4g.micro",
}


# ---------------------------------------------------------------------------
# Cennik
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Pricing:
    # Amazon S3 Standard: (szerokość progu w GB, $/GB-miesiąc)
    s3_tiers: tuple = ((50 * 1024.0, 0.023), (450 * 1024.0, 0.022), (math.inf, 0.021))
    s3_put_per_1k: float = 0.005  # PUT, COPY, POST, LIST
    s3_get_per_1k: float = 0.0004  # GET, SELECT, HEAD
    s3_delete_per_1k: float = 0.0  # DELETE, CANCEL - bezpłatne

    # Amazon DynamoDB On-Demand, klasa Standard
    ddb_wru_per_m: float = 0.625  # $ / 1 mln WRU (1 WRU = zapis do 1 KB)
    ddb_rru_per_m: float = 0.125  # $ / 1 mln RRU (1 RRU = silnie spójny odczyt do 4 KB)
    ddb_rru_per_read: float = 0.5  # brama nie ustawia ConsistentRead -> 0,5 RRU
    ddb_storage_gb_month: float = 0.25
    ddb_free_storage_gb: float = 25.0
    ddb_item_overhead_bytes: int = 100  # narzut indeksowania liczony do storage

    # Amazon S3 Vectors
    s3v_storage_gb_month: float = 0.06
    s3v_put_per_gb: float = 0.20
    s3v_put_min_kb: float = 128.0  # minimalna opłata za jedno PutVectors
    s3v_query_per_m: float = 2.50
    # dane przetworzone na zapytanie: (liczba wektorów w progu, $/TB)
    s3v_proc_tiers: tuple = ((100_000, 0.004), (9_900_000, 0.002), (math.inf, 0.0004))
    s3v_other_per_1k: float = 0.055  # GET/LIST/pozostałe (DeleteVectors - założenie)

    # Amazon Aurora PostgreSQL, Aurora Standard
    aurora_acu_hour: float = 0.12
    aurora_storage_gb_month: float = 0.10
    aurora_io_per_m: float = 0.20

    # Amazon RDS for PostgreSQL, Single-AZ
    rds_micro_hour: float = 0.016
    rds_gp3_gb_month: float = 0.115
    rds_min_storage_gb: float = 20.0

    hours_per_month: float = 730.0

    # --- pochodne ---
    @property
    def s3_put(self) -> float:
        return self.s3_put_per_1k / 1000.0

    @property
    def s3_head(self) -> float:
        return self.s3_get_per_1k / 1000.0

    @property
    def s3_delete(self) -> float:
        return self.s3_delete_per_1k / 1000.0

    @property
    def ddb_read(self) -> float:
        return self.ddb_rru_per_m / 1e6 * self.ddb_rru_per_read

    @property
    def ddb_write(self) -> float:
        return self.ddb_wru_per_m / 1e6

    def s3_storage_cost(self, gb: float) -> float:
        """Miesięczny koszt S3 Standard z progami cenowymi."""
        cost, left = 0.0, max(0.0, gb)
        for width, rate in self.s3_tiers:
            take = min(left, width)
            cost += take * rate
            left -= take
            if left <= 0:
                break
        return cost

    def s3v_processed_cost_per_query(
        self, n_vectors: float, bytes_per_vector: float
    ) -> float:
        """Opłata za dane przetworzone przez jedno QueryVectors przy indeksie n_vectors."""
        cost, left = 0.0, max(0.0, n_vectors)
        for width, rate in self.s3v_proc_tiers:
            take = min(left, width)
            cost += take * bytes_per_vector / TIB * rate
            left -= take
            if left <= 0:
                break
        return cost


# ---------------------------------------------------------------------------
# Scenariusze wywołań (zgodnie z kodem bramy)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    s3_put: float
    s3_head: float
    s3_delete: float
    ddb_reads: float
    ddb_writes: float
    grpc: float
    pg: float
    s3v_query: float
    s3v_put: float
    s3v_delete: float

    @property
    def cloud_calls(self) -> float:
        return (
            self.s3_put
            + self.s3_head
            + self.s3_delete
            + self.ddb_reads
            + self.ddb_writes
        )

    def gateway_api_cost(self, p: Pricing) -> float:
        return (
            self.s3_put * p.s3_put
            + self.s3_head * p.s3_head
            + self.s3_delete * p.s3_delete
            + self.ddb_reads * p.ddb_read
            + self.ddb_writes * p.ddb_write
        )


def build_scenarios(
    name_lookup: bool = False, similar_chunk_hit: float = 0.0
) -> dict[str, Scenario]:
    """
    name_lookup=False (domyślnie): bez wycofanego wyszukiwania po nazwie.
    similar_chunk_hit: odsetek bliskich duplikatów, których pierwszy fragment CDC trafia w indeks
                       (wtedy dochodzi GetItem CHUNK#/FULL# zakończony brakiem trafienia).
    """
    n = 1.0 if name_lookup else 0.0
    h = similar_chunk_hit
    return {
        "new": Scenario(
            "new",
            "Nowy plik (inserted)",
            s3_put=2,
            s3_head=0,
            s3_delete=0,  # JSON + obraz
            ddb_reads=n + 1,  # Query CHUNK# (Limit=1)
            ddb_writes=2,  # PutItem FILE# + PutItem CHUNK#/FULL#
            grpc=1,
            pg=3,  # lock + SELECT najbliższego + INSERT
            s3v_query=1,
            s3v_put=1,
            s3v_delete=0,
        ),
        "exact": Scenario(
            "exact",
            "Dokładna kopia, odrzucona (Y)",
            s3_put=1,
            s3_head=0,
            s3_delete=0,  # tylko JSON
            ddb_reads=n + 2,  # Query CHUNK# + GetItem CHUNK#/FULL#
            ddb_writes=0,
            grpc=0,
            pg=0,
            s3v_query=0,
            s3v_put=0,
            s3v_delete=0,
        ),
        "similar": Scenario(
            "similar",
            "Podobny, odrzucony (X)",
            s3_put=1,
            s3_head=0,
            s3_delete=0,  # tylko JSON
            ddb_reads=n + 1 + h,  # Query CHUNK# (+ ew. GetItem)
            ddb_writes=0,
            grpc=1,
            pg=3,  # lock + SELECT + SELECT FOR UPDATE
            s3v_query=1,
            s3v_put=0,
            s3v_delete=0,
        ),
        "substituted": Scenario(
            "substituted",
            "Podobny, lepszy, podmieniony (Z)",
            s3_put=2,
            s3_head=1,
            s3_delete=2,  # JSON + obraz, HEAD starego, 2 x DELETE
            ddb_reads=n + 2 + h,  # Query CHUNK# + GetItem FILE# (retire)
            ddb_writes=4,  # 2 x PutItem + 2 x DeleteItem
            grpc=1,
            pg=4,  # lock + SELECT + FOR UPDATE + UPDATE
            s3v_query=1,
            s3v_put=1,
            s3v_delete=1,
        ),
    }


# ---------------------------------------------------------------------------
# Rozmiary rekordów (reguły AWS: nazwa atrybutu + wartość, +100 B narzutu na element)
# ---------------------------------------------------------------------------
def _ddb_str(name: str, value: str) -> int:
    return len(name.encode()) + len(value.encode())


def _ddb_num(name: str, value: int) -> int:
    digits = str(abs(int(value))).strip("0") or "0"
    return len(name.encode()) + math.ceil(len(digits) / 2) + 1


@dataclass(frozen=True)
class Workload:
    x_semantic: float = 0.25  # X - duplikaty semantyczne odrzucone
    y_full: float = 0.25  # Y - duplikaty binarne odrzucone
    z_substituted: float = 0.0  # Z - bliskie duplikaty lepszej jakości (podmiana)
    avg_file_mb: float = 2.5
    file_name_len: int = 23  # np. IMG_20260101_123456.jpg
    width: int = 4032
    height: int = 3024
    similar_chunk_hit: float = 0.0
    name_lookup: bool = False

    @property
    def w_new(self) -> float:
        rest = 1.0 - self.x_semantic - self.y_full - self.z_substituted
        if rest < -1e-9:
            raise ValueError("X + Y + Z nie może przekraczać 1,0")
        return max(0.0, rest)

    @property
    def r_storage(self) -> float:
        """Odsetek wolumenu, który nie zajmuje S3 (X + Y + Z; podmiana usuwa stary obraz)."""
        return self.x_semantic + self.y_full + self.z_substituted

    @property
    def files_per_gb(self) -> float:
        return 1024.0 / self.avg_file_mb

    def shares(self) -> dict[str, float]:
        return {
            "new": self.w_new,
            "exact": self.y_full,
            "similar": self.x_semantic,
            "substituted": self.z_substituted,
        }

    # --- rozmiary ---
    def _name(self) -> str:
        return "x" * self.file_name_len

    def ddb_file_item_bytes(self) -> int:
        fn = self._name()
        size_bytes = int(self.avg_file_mb * 1024 * 1024)
        return (
            _ddb_str("PK", "FILE#" + fn)
            + _ddb_str("SK", "META")
            + _ddb_str("S3Key", "payloads/" + fn)
            + _ddb_num("Width", self.width)
            + _ddb_num("Height", self.height)
            + _ddb_num("SizeBytes", size_bytes)
            + _ddb_str("ChunkHash", "h" * 64)
            + _ddb_str("FullHash", "h" * 64)
        )

    def ddb_chunk_item_bytes(self) -> int:
        fn = self._name()
        return (
            _ddb_str("PK", "CHUNK#" + "h" * 64)
            + _ddb_str("SK", "FULL#" + "h" * 64)
            + _ddb_str("S3Key", "payloads/" + fn)
            + _ddb_str("FileName", fn)
        )

    def json_bytes(self, scenario: str) -> int:
        fn = self._name()
        rec = {
            "id": fn,
            "file_name": fn,
            "width": self.width,
            "height": self.height,
            "size_bytes": int(self.avg_file_mb * 1024 * 1024),
            "chunk_hash": "h" * 64,
        }
        if scenario == "exact":
            rec["full_hash"] = "h" * 64
        rec["soft_dedup"] = scenario in ("exact", "similar")
        rec["replace_existing"] = scenario == "substituted"
        if scenario in ("new", "similar", "substituted"):
            rec["quality_score"] = 0.8734512
            rec["semantic_distance"] = 0.1234567
        if scenario in ("similar", "substituted"):
            rec["existing_image_key"] = fn
        return len(json.dumps(rec, separators=(",", ":")))

    def avg_json_bytes_stored(self) -> float:
        """Średni przyrost JSON w S3 na plik (podmiana usuwa stary JSON -> przyrost ~0)."""
        s = self.shares()
        return (
            s["new"] * self.json_bytes("new")
            + s["exact"] * self.json_bytes("exact")
            + s["similar"] * self.json_bytes("similar")
        )


@dataclass(frozen=True)
class Infra:
    backend: str = "local"
    local_fixed_month: float = 0.0  # prąd/VPS dla lokalnego PostgreSQL (domyślnie 0)
    embedding_dims: int = 512  # SSCD disc_mixup
    s3v_metadata_bytes: int = 21  # {"quality_score": float}
    s3v_put_batch: int = 1  # brama przetwarza obrazy pojedynczo
    pg_bytes_per_vector: int = 4608  # wiersz + HNSW + indeksy B-tree (szacunek)
    pg_io_per_statement: float = 1.0  # Aurora Standard: I/O na instrukcję (szacunek)
    aurora_active_acu: float = 1.0
    ingest_files_per_hour: float = 3600.0  # przepustowość etapu podobieństwa (szacunek)
    ddb_free_tier: bool = False

    def s3v_vector_bytes(self, file_name_len: int) -> int:
        return self.embedding_dims * 4 + file_name_len + self.s3v_metadata_bytes


# ---------------------------------------------------------------------------
# Symulacja miesiąc po miesiącu
# ---------------------------------------------------------------------------
def simulate(
    volumes_gb: list[float], wl: Workload, infra: Infra, p: Pricing
) -> list[dict]:
    """
    volumes_gb[m] - wolumen wgrany na początku miesiąca m+1.
    Zwraca listę miesięcznych zapisów z kosztami baseline i narzędzia.
    """
    sc = build_scenarios(wl.name_lookup, wl.similar_chunk_hit)
    shares = wl.shares()
    fpg = wl.files_per_gb
    ddb_unique_bytes = (
        wl.ddb_file_item_bytes()
        + wl.ddb_chunk_item_bytes()
        + 2 * p.ddb_item_overhead_bytes
    )
    vec_bytes = infra.s3v_vector_bytes(wl.file_name_len)

    base_gb = tool_payload_gb = json_gb = ddb_gb = 0.0
    n_vectors = 0.0
    cum = 0.0
    ledger = []
    for m, v in enumerate(volumes_gb, start=1):
        files = v * fpg
        cnt = {k: files * s for k, s in shares.items()}

        # --- jednorazowe (kohorta miesiąca) ---
        base_once = files * p.s3_put
        gw_api = sum(cnt[k] * sc[k].gateway_api_cost(p) for k in sc)
        pg_files = cnt["new"] + cnt["similar"] + cnt["substituted"]
        pg_stmts = sum(cnt[k] * sc[k].pg for k in sc)
        n_start = n_vectors
        n_vectors += cnt["new"]  # podmiana aktualizuje wiersz w miejscu
        n_avg = (n_start + n_vectors) / 2.0

        vec_once = 0.0
        vec_fixed = 0.0
        vec_storage = 0.0
        if infra.backend == "local":
            vec_fixed = infra.local_fixed_month
        elif infra.backend == "s3vectors":
            q = sum(cnt[k] * sc[k].s3v_query for k in sc)
            puts = sum(cnt[k] * sc[k].s3v_put for k in sc)
            dels = sum(cnt[k] * sc[k].s3v_delete for k in sc)
            b = max(1, infra.s3v_put_batch)
            put_bytes_per_vec = max(p.s3v_put_min_kb * 1024, b * vec_bytes) / b
            vec_once = (
                q * p.s3v_query_per_m / 1e6
                + q * p.s3v_processed_cost_per_query(n_avg, vec_bytes)
                + puts * put_bytes_per_vec / GIB * p.s3v_put_per_gb
                + dels * p.s3v_other_per_1k / 1000.0
            )
            vec_storage = n_vectors * vec_bytes / GIB * p.s3v_storage_gb_month
        elif infra.backend in ("aurora_pause", "aurora_min"):
            min_acu = 0.0 if infra.backend == "aurora_pause" else 0.5
            active_h = pg_files / infra.ingest_files_per_hour
            vec_fixed = min_acu * p.hours_per_month * p.aurora_acu_hour
            vec_once = (
                max(0.0, infra.aurora_active_acu - min_acu)
                * active_h
                * p.aurora_acu_hour
                + pg_stmts * infra.pg_io_per_statement / 1e6 * p.aurora_io_per_m
            )
            vec_storage = (
                n_vectors * infra.pg_bytes_per_vector / GIB * p.aurora_storage_gb_month
            )
        elif infra.backend == "rds_micro":
            vec_fixed = p.rds_micro_hour * p.hours_per_month
            pg_gb = n_vectors * infra.pg_bytes_per_vector / GIB
            vec_storage = max(p.rds_min_storage_gb, pg_gb) * p.rds_gp3_gb_month
        else:
            raise ValueError(infra.backend)

        # --- stany magazynów (narastająco) ---
        base_gb += v
        tool_payload_gb += v * (1.0 - wl.r_storage)
        json_gb += files * wl.avg_json_bytes_stored() / GIB
        ddb_gb += cnt["new"] * ddb_unique_bytes / GIB

        base_storage = p.s3_storage_cost(base_gb)
        tool_s3 = p.s3_storage_cost(tool_payload_gb + json_gb)
        billable_ddb = max(
            0.0, ddb_gb - (p.ddb_free_storage_gb if infra.ddb_free_tier else 0.0)
        )
        tool_ddb = billable_ddb * p.ddb_storage_gb_month

        tool_once = gw_api + vec_once
        tool_rec = tool_s3 + tool_ddb + vec_storage + vec_fixed
        base_rec = base_storage
        net_once = base_once - tool_once
        net_rec = base_rec - tool_rec
        cum_start = cum + net_once
        cum = cum_start + net_rec
        ledger.append(
            {
                "month": m,
                "volume_gb": v,
                "files": files,
                "base_once": base_once,
                "base_rec": base_rec,
                "gw_api": gw_api,
                "vec_once": vec_once,
                "tool_s3": tool_s3,
                "tool_ddb": tool_ddb,
                "vec_storage": vec_storage,
                "vec_fixed": vec_fixed,
                "tool_once": tool_once,
                "tool_rec": tool_rec,
                "net_once": net_once,
                "net_rec": net_rec,
                "net_month": net_once + net_rec,
                "cum_start": cum_start,
                "cum": cum,
                "ddb_gb": ddb_gb,
                "json_gb": json_gb,
                "n_vectors": n_vectors,
                "base_gb": base_gb,
                "tool_payload_gb": tool_payload_gb,
            }
        )
    return ledger


def payback_months(ledger: list[dict]) -> float:
    """Ułamkowy miesiąc, w którym skumulowany wynik przechodzi przez zero (inf - nigdy)."""
    prev = 0.0
    for row in ledger:
        start = prev + row["net_once"]
        if start >= 0:
            return row["month"] - 1.0
        if row["net_rec"] > 0 and start + row["net_rec"] >= 0:
            return row["month"] - 1.0 + (-start) / row["net_rec"]
        prev = row["cum"]
    return math.inf


def _bisect(f, lo: float = 1e-3, hi: float = 1e5, iters: int = 80) -> float:
    """Najmniejsze x z [lo, hi], dla którego f(x) >= 0 (0.0 oznacza: każdy dodatni wolumen)."""
    if f(lo) >= 0:
        return 0.0
    if f(hi) < 0:
        return math.inf
    for _ in range(iters):
        mid = (lo + hi) / 2
        if f(mid) >= 0:
            hi = mid
        else:
            lo = mid
    return hi


def static_breakeven_volume(months: int, wl, infra, p) -> float:
    return _bisect(
        lambda v: simulate([v] + [0.0] * (months - 1), wl, infra, p)[-1]["cum"]
    )


def static_viability_volume(wl, infra, p) -> float:
    """Minimalny zbiór, przy którym miesięczne utrzymanie po wgraniu jest na plus."""
    return _bisect(lambda v: simulate([v, 0.0], wl, infra, p)[-1]["net_rec"])


def continuous_breakeven_monthly(months: int, wl, infra, p) -> float:
    return _bisect(lambda v: simulate([v] * months, wl, infra, p)[-1]["cum"], hi=1e5)


# ---------------------------------------------------------------------------
# Wzory zamknięte (pgvector lokalnie, płaska stawka S3, bez free tier) - kontrola
# ---------------------------------------------------------------------------
def closed_form(wl: Workload, infra: Infra, p: Pricing) -> dict:
    sc = build_scenarios(wl.name_lookup, wl.similar_chunk_hit)
    sh = wl.shares()
    fpg = wl.files_per_gb
    c_s3 = p.s3_tiers[0][1]
    api_per_file = sum(sh[k] * sc[k].gateway_api_cost(p) for k in sc)
    c_api = fpg * api_per_file  # C_api [$ / GB]
    c_base = fpg * p.s3_put  # C_put0 [$ / GB]
    i = c_api - c_base  # I [$ / GB] - jednorazowy narzut
    ddb_unique = (
        wl.ddb_file_item_bytes()
        + wl.ddb_chunk_item_bytes()
        + 2 * p.ddb_item_overhead_bytes
    )
    h_json = fpg * wl.avg_json_bytes_stored() / GIB  # GB JSON na 1 GB wejścia
    h_ddb = fpg * sh["new"] * ddb_unique / GIB  # GB DynamoDB na 1 GB wejścia
    gross = wl.r_storage * c_s3
    maint = h_json * c_s3 + h_ddb * p.ddb_storage_gb_month
    s = gross - maint  # S [$ / GB-miesiąc]
    F = infra.local_fixed_month
    out = {
        "c_s3": c_s3,
        "api_per_file": api_per_file,
        "c_api": c_api,
        "c_base": c_base,
        "i": i,
        "h_json": h_json,
        "h_ddb": h_ddb,
        "gross": gross,
        "maint": maint,
        "s": s,
        "F": F,
        "ddb_unique_bytes": ddb_unique,
        "T_A": (i / s) if s > 0 else math.inf,
    }
    out["V_min"] = (F / s) if s > 0 else math.inf
    out["V_A"] = {
        M: (M * F / (M * s - i)) if M * s - i > 0 else math.inf for M in HORIZONS
    }
    out["v_B"] = {
        M: (F / (s * (M + 1) / 2 - i)) if s * (M + 1) / 2 - i > 0 else math.inf
        for M in HORIZONS
    }
    out["R_min"] = (i + maint) / c_s3  # minimalne R dla zwrotu w 1 miesiąc przy F=0
    return out


# ---------------------------------------------------------------------------
# Raport terminalowy
# ---------------------------------------------------------------------------
def fmt_money(x: float, d: int = 4) -> str:
    return "nigdy" if math.isinf(x) else f"${x:,.{d}f}"


def fmt_gb(x: float) -> str:
    if math.isinf(x):
        return "nieosiągalny"
    if x == 0.0:
        return ">0 GB (każdy)"
    return f"{x:,.2f} GB"


def fmt_cell(x: float, dec: int = 0) -> str:
    if math.isinf(x):
        return "∞"
    if x == 0.0:
        return ">0 (każdy)"
    return f"{x:,.{dec}f}"


def fmt_months(x: float) -> str:
    if math.isinf(x):
        return "nigdy (w horyzoncie)"
    return f"{x:,.2f} mies. (~{x * 30.4375:,.1f} dni)"


def print_report(args, wl: Workload, base_infra: Infra, p: Pricing) -> None:
    sc = build_scenarios(wl.name_lookup, wl.similar_chunk_hit)
    sh = wl.shares()
    line = "=" * 100
    sub = "-" * 100
    print(line)
    print(
        f" RAPORT KOŃCOWY FINOPS: s3-dedup-engine | cennik AWS us-east-1 z dnia {PRICING_DATE}"
    )
    print(line)

    # [1] Parametry
    print("\n[1] PARAMETRY")
    print(f"    X (semantyczne, odrzucone)   : {wl.x_semantic:.2%}")
    print(f"    Y (binarne 1:1, odrzucone)   : {wl.y_full:.2%}")
    print(f"    Z (podmiana lepszą jakością) : {wl.z_substituted:.2%}")
    print(f"    Nowe unikalne (1-X-Y-Z)      : {wl.w_new:.2%}")
    print(f"    R_s = X+Y+Z (bez miejsca)    : {wl.r_storage:.2%}")
    print(
        f"    Średni plik                  : {wl.avg_file_mb:.2f} MB -> {wl.files_per_gb:,.1f} plików/GB"
    )
    print(
        f"    Wyszukiwanie po nazwie       : {'tak' if wl.name_lookup else 'nie (etap wycofany)'}"
    )
    print(
        f"    DynamoDB free tier 25 GB     : {'tak' if base_infra.ddb_free_tier else 'nie (wariant ostrożny)'}"
    )
    print(
        f"    Koszt stały lokalnego PG     : ${base_infra.local_fixed_month:.2f} / mies."
    )

    # [2] Scenariusze
    print("\n[2] WYWOŁANIA NA JEDEN PLIK (wg kodu bramy)")
    print(
        f"    {'Scenariusz':<36}{'PUT':>5}{'HEAD':>6}{'DEL':>5}{'DDB R':>7}{'DDB W':>7}{'Płatne':>8}{'gRPC':>6}{'PG':>4}{'$ / 1000 plików':>18}"
    )
    for k, s in sc.items():
        print(
            f"    {s.name:<36}{s.s3_put:>5.0f}{s.s3_head:>6.0f}{s.s3_delete:>5.0f}{s.ddb_reads:>7.2f}"
            f"{s.ddb_writes:>7.0f}{s.cloud_calls:>8.2f}{s.grpc:>6.0f}{s.pg:>4.0f}"
            f"{s.gateway_api_cost(p) * 1000:>18.6f}"
        )
    print(
        f"    {'Baseline: bezpośredni PutObject':<36}{1:>5}{0:>6}{0:>5}{0:>7}{0:>7}{1:>8}{0:>6}{0:>4}{p.s3_put * 1000:>18.6f}"
    )

    # [3] Rekordy
    print("\n[3] REKORDY PRZECHOWYWANE NA STAŁE")
    fi, ci = wl.ddb_file_item_bytes(), wl.ddb_chunk_item_bytes()
    print(
        f"    DynamoDB FILE#<nazwa> / META             : {fi} B danych + {p.ddb_item_overhead_bytes} B narzutu = {fi + 100} B"
    )
    print(
        f"    DynamoDB CHUNK#<sha256> / FULL#<sha256>  : {ci} B danych + {p.ddb_item_overhead_bytes} B narzutu = {ci + 100} B"
    )
    print(
        "    (pełny skrót jest kluczem sortowania SK=FULL#..., nie osobnym elementem)"
    )
    for k in ("new", "exact", "similar", "substituted"):
        print(f"    S3 metadata/<plik>.json ({sc[k].name:<34}): {wl.json_bytes(k)} B")
    print(
        f"    Wektor S3 Vectors (dane + klucz + metadane)  : {base_infra.s3v_vector_bytes(wl.file_name_len)} B"
    )
    print(
        f"    Wektor w PostgreSQL (wiersz + HNSW, szacunek): {base_infra.pg_bytes_per_vector} B"
    )

    # [4] Operacje dla zbioru V
    V = args.dataset_gb
    files = round(V * wl.files_per_gb)
    cnt = {k: round(files * s) for k, s in sh.items()}
    cnt["new"] = files - cnt["exact"] - cnt["similar"] - cnt["substituted"]
    tot = {
        f: sum(cnt[k] * getattr(sc[k], f) for k in sc)
        for f in (
            "s3_put",
            "s3_head",
            "s3_delete",
            "ddb_reads",
            "ddb_writes",
            "grpc",
            "pg",
            "s3v_query",
            "s3v_put",
            "s3v_delete",
        )
    }
    c_put, c_head = tot["s3_put"] * p.s3_put, tot["s3_head"] * p.s3_head
    c_r, c_w = tot["ddb_reads"] * p.ddb_read, tot["ddb_writes"] * p.ddb_write
    paid = (
        tot["s3_put"]
        + tot["s3_head"]
        + tot["s3_delete"]
        + tot["ddb_reads"]
        + tot["ddb_writes"]
    )
    print(f"\n[4] ILE OPERACJI WYKONAŁ SYSTEM (zbiór {V:,.0f} GB = {files:,} plików)")
    print(
        f"    Kohorty: nowe {cnt['new']:,} | Y {cnt['exact']:,} | X {cnt['similar']:,} | Z {cnt['substituted']:,}"
    )
    print(
        f"    S3 PutObject                  : {tot['s3_put']:>14,.0f}   {fmt_money(c_put)}"
    )
    print(
        f"    S3 HeadObject                 : {tot['s3_head']:>14,.0f}   {fmt_money(c_head)}"
    )
    print(
        f"    S3 DeleteObject               : {tot['s3_delete']:>14,.0f}   $0.0000 (bezpłatne)"
    )
    print(
        f"    DynamoDB Query/GetItem        : {tot['ddb_reads']:>14,.0f}   {fmt_money(c_r)}"
    )
    print(
        f"    DynamoDB PutItem/DeleteItem   : {tot['ddb_writes']:>14,.0f}   {fmt_money(c_w)}"
    )
    print(
        f"    RAZEM płatne S3 + DynamoDB    : {paid:>14,.0f}   {fmt_money(c_put + c_head + c_r + c_w)}"
    )
    print(
        f"    Baseline (1 PutObject/plik)   : {files:>14,}   {fmt_money(files * p.s3_put)}"
    )
    print(f"    gRPC (lokalnie)               : {tot['grpc']:>14,.0f}")
    print(f"    Instrukcje PostgreSQL         : {tot['pg']:>14,.0f}")
    print(
        f"    Gdyby indeks był w S3 Vectors : QueryVectors {tot['s3v_query']:,.0f} | PutVectors {tot['s3v_put']:,.0f} | DeleteVectors {tot['s3v_delete']:,.0f}"
    )

    # [5] Miejsce
    led = simulate([V, 0.0], wl, base_infra, p)
    r1 = led[0]
    saved_gross = V * wl.r_storage
    saved_net = saved_gross - r1["json_gb"] - r1["ddb_gb"]
    print("\n[5] ILE MIEJSCA ZAOSZCZĘDZIŁ SYSTEM")
    print(
        f"    Niezapisane/usunięte obrazy        : {cnt['exact'] + cnt['similar'] + cnt['substituted']:,} plików"
    )
    print(
        f"    Oszczędność brutto (R_s * V)       : {saved_gross:,.2f} GB ({saved_gross / 1024:.3f} TB)"
    )
    print(f"    Obrazy faktycznie w S3             : {r1['tool_payload_gb']:,.2f} GB")
    print(f"    Narzut JSON w S3                   : {r1['json_gb'] * 1024:,.2f} MB")
    print(f"    Narzut DynamoDB (FILE# + CHUNK#)   : {r1['ddb_gb'] * 1024:,.2f} MB")
    print(f"    Oszczędność NETTO                  : {saved_net:,.2f} GB")
    print(f"    S3 bez narzędzia                   : ${r1['base_rec']:,.2f} / mies.")
    print(
        f"    S3 + DynamoDB z narzędziem         : ${r1['tool_s3'] + r1['tool_ddb']:,.2f} / mies."
    )
    print(
        f"    Miesięczna oszczędność utrzymania  : ${r1['base_rec'] - r1['tool_s3'] - r1['tool_ddb']:,.2f} / mies."
    )

    # [6] Koszt odrzucenia
    file_gb = wl.avg_file_mb / 1024.0
    c_s3 = p.s3_tiers[0][1]
    print(
        "\n[6] KOSZT ODRZUCENIA DUPLIKATU vs PODATEK OD UNIKALNEGO PLIKU (pgvector lokalnie)"
    )
    for k in ("exact", "similar", "substituted", "new"):
        s = sc[k]
        delta = s.gateway_api_cost(p) - p.s3_put
        json_m = wl.json_bytes(k) / GIB * c_s3 if k != "substituted" else 0.0
        if k == "new":
            ddb_m = (fi + ci + 200) / GIB * p.ddb_storage_gb_month
            print(
                f"    {s.name:<36}: narzut API {delta * 1e6:+9.3f} µ$, utrzymanie {(json_m + ddb_m) * 1e6:7.3f} µ$/mies., oszczędza 0"
            )
        else:
            save_m = file_gb * c_s3 - json_m
            t = delta / save_m if save_m > 0 else math.inf
            print(
                f"    {s.name:<36}: narzut API {delta * 1e6:+9.3f} µ$, oszczędza {save_m * 1e6:8.3f} µ$/mies. -> zwrot {t * 30.4375 * 24:,.2f} h"
            )

    # [7] Wzory zamknięte
    cf = closed_form(wl, base_infra, p)
    print(
        "\n[7] WZORY ZAMKNIĘTE (pgvector lokalnie, płaska stawka S3 pierwszego progu)"
    )
    print(
        f"    C_api  = {cf['c_api']:.6f} $/GB   C_put0 = {cf['c_base']:.6f} $/GB   I = C_api - C_put0 = {cf['i']:.6f} $/GB"
    )
    print(
        f"    R_s*C_s3 = {cf['gross']:.6f} $/GB-mies.   K_utrz = {cf['maint']:.8f} $/GB-mies.   S = {cf['s']:.6f} $/GB-mies."
    )
    print(
        f"    T_A = I / S = {fmt_months(cf['T_A'])}   |  minimalne R_s dla zwrotu w 1 mies. = {cf['R_min']:.2%}"
    )
    if cf["F"] > 0:
        print(f"    V_min = F / S = {fmt_gb(cf['V_min'])}")
        for M in HORIZONS:
            print(
                f"    M={M:>2}: V_A = {fmt_gb(cf['V_A'][M]):>16}   v_B = {fmt_gb(cf['v_B'][M]):>16} / mies."
            )

    # [8] Wariant A
    print("\n" + sub)
    print(
        " [8] WARIANT A: zbiór wgrany raz, potem nic nie przybywa; S3 + DynamoDB + indeks stoją"
    )
    print(sub)
    hdr = f"    {'Backend wektorowy':<46}{'utrzym./mies.':>14}{'oszcz./mies.':>14}{'zwrot dla V':>26}{'V_min':>16}"
    print(hdr)
    results_a = {}
    for b in BACKENDS:
        inf = replace(base_infra, backend=b)
        led = simulate([V] + [0.0] * 119, wl, inf, p)
        maint = led[1]["tool_rec"]
        save = led[1]["base_rec"]
        pb = payback_months(led)
        vmin = static_viability_volume(wl, inf, p)
        results_a[b] = (led, pb, vmin)
        print(
            f"    {BACKEND_NAMES[b]:<46}{fmt_money(maint, 2):>14}{fmt_money(save, 2):>14}{fmt_months(pb):>26}{fmt_gb(vmin):>16}"
        )
    print(
        "\n    Minimalny jednorazowy zbiór V, aby po M miesiącach stania wyjść na zero:"
    )
    print(f"    {'Backend':<46}" + "".join(f"{'M=' + str(M):>12}" for M in HORIZONS))
    for b in BACKENDS:
        inf = replace(base_infra, backend=b)
        vals = [static_breakeven_volume(M, wl, inf, p) for M in HORIZONS]
        print(
            f"    {BACKEND_NAMES[b]:<46}"
            + "".join(f"{fmt_cell(x, 0):>12}" for x in vals)
        )

    # [9] Wariant B
    print("\n" + sub)
    print(" [9] WARIANT B: pliki napływają co miesiąc (ciągłe ładowanie na S3)")
    print(sub)
    vm = args.monthly_gb
    print(f"    Zwrot przy napływie {vm:,.0f} GB / mies.:")
    for b in BACKENDS:
        inf = replace(base_infra, backend=b)
        led = simulate([vm] * 120, wl, inf, p)
        pb = payback_months(led)
        first_pos = next((r["month"] for r in led if r["net_month"] > 0), math.inf)
        print(
            f"    {BACKEND_NAMES[b]:<46} zwrot: {fmt_months(pb):<28} pierwszy miesiąc na plus: {first_pos}"
        )
    print(
        "\n    Minimalny miesięczny napływ v [GB/mies.], aby po M miesiącach wyjść na zero:"
    )
    print(f"    {'Backend':<46}" + "".join(f"{'M=' + str(M):>12}" for M in HORIZONS))
    for b in BACKENDS:
        inf = replace(base_infra, backend=b)
        vals = [continuous_breakeven_monthly(M, wl, inf, p) for M in HORIZONS]
        print(
            f"    {BACKEND_NAMES[b]:<46}"
            + "".join(f"{fmt_cell(x, 1):>12}" for x in vals)
        )
    print(line)


def main() -> None:
    ap = argparse.ArgumentParser(description="FinOps break-even dla s3-dedup-engine")
    ap.add_argument(
        "-x",
        "--semantic-dups",
        type=float,
        default=0.25,
        help="X: udział duplikatów semantycznych",
    )
    ap.add_argument(
        "-y",
        "--full-dups",
        type=float,
        default=0.25,
        help="Y: udział duplikatów binarnych",
    )
    ap.add_argument(
        "-z",
        "--substituted",
        type=float,
        default=0.0,
        help="Z: udział podmian lepszą jakością",
    )
    ap.add_argument(
        "--size-mb", type=float, default=2.5, help="średni rozmiar pliku [MB]"
    )
    ap.add_argument(
        "--name-len", type=int, default=23, help="średnia długość nazwy pliku [znaki]"
    )
    ap.add_argument(
        "--dataset-gb", type=float, default=1000.0, help="V: zbiór wariantu A [GB]"
    )
    ap.add_argument(
        "--monthly-gb",
        type=float,
        default=100.0,
        help="v: napływ wariantu B [GB/mies.]",
    )
    ap.add_argument(
        "--local-fixed",
        type=float,
        default=0.0,
        help="koszt stały lokalnego PostgreSQL [$/mies.]",
    )
    ap.add_argument(
        "--with-name-lookup",
        action="store_true",
        help="dolicz wycofany GetItem FILE# na plik",
    )
    ap.add_argument(
        "--similar-chunk-hit",
        type=float,
        default=0.0,
        help="odsetek bliskich duplikatów z trafieniem CDC",
    )
    ap.add_argument(
        "--strong-reads", action="store_true", help="odczyty silnie spójne (1 RRU)"
    )
    ap.add_argument(
        "--ddb-free-tier",
        action="store_true",
        help="uwzględnij 25 GB bezpłatnego storage DynamoDB",
    )
    ap.add_argument(
        "--s3v-batch", type=int, default=1, help="liczba wektorów w jednym PutVectors"
    )
    ap.add_argument(
        "--pg-bytes-per-vector",
        type=int,
        default=4608,
        help="bajty na wektor w PostgreSQL (z HNSW)",
    )
    ap.add_argument(
        "--ingest-files-per-hour",
        type=float,
        default=3600.0,
        help="przepustowość etapu podobieństwa",
    )
    ap.add_argument(
        "--aurora-active-acu",
        type=float,
        default=1.0,
        help="średnie ACU podczas przetwarzania",
    )
    args = ap.parse_args()

    p = Pricing(ddb_rru_per_read=1.0 if args.strong_reads else 0.5)
    wl = Workload(
        x_semantic=args.semantic_dups,
        y_full=args.full_dups,
        z_substituted=args.substituted,
        avg_file_mb=args.size_mb,
        file_name_len=args.name_len,
        similar_chunk_hit=args.similar_chunk_hit,
        name_lookup=args.with_name_lookup,
    )
    infra = Infra(
        local_fixed_month=args.local_fixed,
        s3v_put_batch=args.s3v_batch,
        pg_bytes_per_vector=args.pg_bytes_per_vector,
        ingest_files_per_hour=args.ingest_files_per_hour,
        aurora_active_acu=args.aurora_active_acu,
        ddb_free_tier=args.ddb_free_tier,
    )
    print_report(args, wl, infra, p)


if __name__ == "__main__":
    main()
