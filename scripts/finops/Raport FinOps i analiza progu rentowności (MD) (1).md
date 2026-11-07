# Raport FinOps: Model kosztów, analiza wywołań API i próg rentowności (*break-even*) dwuetapowej deduplikacji obiektów S3

Dokument przedstawia zweryfikowany względem kodu źródłowego (`services/gateway` oraz `services/ai_worker`) model finansowy silnika deduplikacji obrazów przed zapisem do Amazon S3. Model uwzględnia:
- pełną macierz wywołań API Amazon S3 (`PutObject`, `HeadObject`, `DeleteObject`) oraz Amazon DynamoDB (`Query`, `GetItem`, `PutItem`, `DeleteItem`) w aktualnej architekturze po wycofaniu potoku wyszukiwania po nazwie pliku (`FileNameMatchPipeline`),
- **stałe (nigdy niewyłączane) utrzymywanie tabeli Amazon DynamoDB** przechowującej rekordy `PK = FILE#<fileName>, SK = META` oraz `PK = CHUNK#<chunkHash>, SK = FULL#<fullHash>`, a także obiektów raportowych `metadata/<fileName>.json` w Amazon S3,
- rozbicie kosztu pojedynczego odrzucenia duplikatu kryptograficznego (`FullDuplicate`, udział $Y$) i semantycznego (`SemanticDuplicate`, udział $X$) względem narzutu indeksowania plików unikalnych ($1 - X - Y - Z$),
- dwa reżimy eksploatacji: **Wariant A (jednorazowy wsad $V$ i dalsze stałe utrzymywanie S3 + DynamoDB + indeksu wektorowego bez nowych plików)** oraz **Wariant B (ciągły napływ $v\text{ GB/mies.}$ przy stale rosnącym wolumenie S3, DynamoDB i indeksu wektorowego)**,
- porównanie pięciu architektur przechowywania i przeszukiwania wektorów CLIP (`512 x float32`): lokalnego PostgreSQL z `pgvector`, **Amazon S3 Vectors (Vector Buckets)**, **Amazon Aurora PostgreSQL Serverless v2 z `pgvector`** (z auto-pauzą `0 ACU` oraz bez auto-pauzy `0.5 ACU`) oraz **Amazon RDS for PostgreSQL (`db.t4g.micro`) z `pgvector`**.

---

## 1. Cennik referencyjny usług chmurowych AWS (region `us-east-1`, weryfikacja: 2026-10-25)

Wszystkie obliczenia w skrypcie `finops_breakeven.py` oraz w niniejszym raporcie opierają się na oficjalnych stawkach *on-demand* dla regionu `us-east-1` (N. Virginia), zweryfikowanych na dzień **25 października 2026 r.** Domyślny profil skryptu wyłącza darmowy próg DynamoDB (`25 GB`), aby wykazać pełny, niesubsydiowany koszt utrzymania indeksów `FILE#` oraz `CHUNK#/FULL#` (przełącznik `--ddb-free-tier` pozwala uwzględnić darmowe `25 GB`).

| Usługa i operacja / zasób | Stawka jednostkowa (USD) | Przelicznik w modelu | Jednostka rozliczeniowa i reguły taryfikacji | Źródło i data weryfikacji |
| :--- | :--- | :--- | :--- | :--- |
| **Amazon S3 Standard — Storage** (do $50\text{ TB/mies.}$) | $\$0.023 / \text{GB-mies.}$ | $C_{\text{S3}} = 0.023$ | Rozliczane za każdy $\text{GiB}$ ($2^{30}\text{ B}$) przechowywanych obiektów `payloads/*` i `metadata/*.json` | [Amazon S3 Pricing](https://aws.amazon.com/s3/pricing/) (2026-10-25) |
| **Amazon S3 Standard — `PutObject` / `CopyObject` / `POST` / `LIST`** | $\$0.005 / 1\,000\text{ żądań}$ | $c_{\text{put}} = 5.0 \times 10^{-6}$ | Każde wywołanie `PutObject` dla obrazu oraz dla raportu `metadata/<file>.json` | [Amazon S3 Pricing](https://aws.amazon.com/s3/pricing/), [PutObject API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html) (2026-10-25) |
| **Amazon S3 Standard — `GetObject` / `HeadObject` / `SELECT`** | $\$0.0004 / 1\,000\text{ żądań}$ | $c_{\text{head}} = 0.4 \times 10^{-6}$ | Wywołanie `HeadObject` w metodzie `PayloadBytes` podczas podmiany gorszego duplikatu (`retireReplaced`) | [Amazon S3 Pricing](https://aws.amazon.com/s3/pricing/), [HeadObject API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html) (2026-10-25) |
| **Amazon S3 Standard — `DeleteObject`** | $\$0.00$ (bezpłatne) | $c_{\text{del}} = 0.0$ | Usuwanie zastąpionego obiektu i jego starego pliku JSON z S3 nie generuje opłat za żądanie | [Amazon S3 Pricing](https://aws.amazon.com/s3/pricing/) (2026-10-25) |
| **Amazon DynamoDB On-Demand — Odczyt (`GetItem`, `Query`)** | $\$0.125 / 1\,000\,000\text{ RRU}$ | $c_{\text{rru}} = 0.0625 \times 10^{-6}$ (0.5 RRU) | Odczyt *eventually consistent* do $4\text{ KB}$ zużywa $0.5\text{ RRU}$ ($\approx \$0.0625 / 1\text{ mln}$ odczytów); *strongly consistent* (`--ddb-strong-reads`) zużywa $1.0\text{ RRU}$ | [Amazon DynamoDB Pricing](https://aws.amazon.com/dynamodb/pricing/), [Read/Write Operations](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/read-write-operations.html) (2026-10-25) |
| **Amazon DynamoDB On-Demand — Zapis (`PutItem`, `DeleteItem`)** | $\$0.625 / 1\,000\,000\text{ WRU}$ | $c_{\text{wru}} = 0.625 \times 10^{-6}$ (1 WRU) | Każdy zapis lub usunięcie rekordu do $1\text{ KB}$ zużywa $1.0\text{ WRU}$ | [Amazon DynamoDB Pricing](https://aws.amazon.com/dynamodb/pricing/) (2026-10-25) |
| **Amazon DynamoDB — Przechowywanie tabeli (Storage)** | $\$0.25 / \text{GB-mies.}$ | $C_{\text{DDB}} = 0.25$ | Rozmiar rekordu = suma bajtów nazw i wartości atrybutów $+ 100\text{ B}$ narzutu indeksowego na każdy element (`FILE#` i `CHUNK#/FULL#`) | [Amazon DynamoDB Pricing](https://aws.amazon.com/dynamodb/pricing/), [Item Size Calculations](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/CapacityUnitCalculations.html) (2026-10-25) |
| **Amazon S3 Vectors — Storage** | $\$0.06 / \text{GB-mies.}$ | $C_{\text{S3V,stor}} = 0.06$ | Rozmiar logiczny wektora (`4 B × wymiar`) + klucz + metadane | [Amazon S3 Pricing — S3 Vectors](https://aws.amazon.com/s3/pricing/) (2026-10-25) |
| **Amazon S3 Vectors — `PutVectors` (zapis/aktualizacja)** | $\$0.20 / \text{GB}$ | $\min 128\text{ KB}$ na wywołanie | Pojedyncze wywołanie `PutVectors` rozlicza minimum $128\text{ KB}$ ($\approx \$0.00002441$ przy `batch=1`) | [Amazon S3 Pricing — S3 Vectors](https://aws.amazon.com/s3/pricing/), [PutVectors API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_S3VectorBuckets_PutVectors.html) (2026-10-25) |
| **Amazon S3 Vectors — `QueryVectors` (zapytania ANN)** | $\$2.50 / 1\,000\,000\text{ zapytań}$ + koszt przetworzonych danych | $c_{\text{s3v,q}} = 2.5 \times 10^{-6}$ + $\$0.004/\text{TB}$ (do $100\text{ tys.}$ wektorów) | Przeszukiwane dane rozliczane progowo: $\$0.004/\text{TB}$ dla pierwszych $100\text{ tys.}$ wektorów w indeksie, $\$0.002/\text{TB}$ dla $100\text{ tys.} - 1\text{ mln}$ oraz $\$0.0004/\text{TB}$ powyżej $1\text{ mln}$ wektorów | [Amazon S3 Pricing — S3 Vectors](https://aws.amazon.com/s3/pricing/), [S3 Vectors API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_Operations_Amazon_S3_Vectors.html) (2026-10-25) |
| **Amazon S3 Vectors — `DeleteVectors`** | $\$0.055 / 1\,000\text{ żądań}$ | $c_{\text{s3v,del}} = 5.5 \times 10^{-5}$ | Wywoływane wyłącznie przy podmianie obrazu na wyższą jakość ($Z > 0$), gdy zmienia się nazwa pliku | [Amazon S3 Pricing — S3 Vectors](https://aws.amazon.com/s3/pricing/) (2026-10-25) |
| **Amazon Aurora PostgreSQL Serverless v2 — Compute (`pgvector`)** | $\$0.12 / \text{ACU-godz.}$ | $0\text{ ACU}$ (auto-pauza) lub $0.5\text{ ACU}$ ($\$43.80/\text{mies.}$) | Konfiguracja *Standard* z włączoną auto-pauzą skaluje się do $0\text{ ACU}$ po okresie bezczynności; bez auto-pauzy minimum to $0.5\text{ ACU}$ ($730\text{ h} \times 0.5 \times \$0.12 = \$43.80/\text{mies.}$) | [Amazon Aurora Pricing](https://aws.amazon.com/rds/aurora/pricing/), [Aurora Auto-Pause](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html) (2026-10-25) |
| **Amazon Aurora PostgreSQL — Storage & I/O** | $\$0.10 / \text{GB-mies.}$ oraz $\$0.20 / 1\,000\,000\text{ I/O}$ | $C_{\text{Aur,stor}} = 0.10$ | Rozliczane za faktycznie zajęty rozmiar tabeli i indeksu HNSW w `pgvector` bez konieczności alokacji z góry | [Amazon Aurora Pricing](https://aws.amazon.com/rds/aurora/pricing/), [Aurora pgvector](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraPostgreSQLReleaseNotes/AuroraPostgreSQL.Extensions.html) (2026-10-25) |
| **Amazon RDS for PostgreSQL — `db.t4g.micro` (Single-AZ) + `gp3`** | $\$0.016 / \text{godz.}$ ($\$11.68/\text{mies.}$) + $\$0.115 / \text{GB-mies.}$ | Minimum $20\text{ GiB}$ `gp3` ($\$2.30/\text{mies.}$) $\Rightarrow \$13.98/\text{mies.}$ | Najmniejsza instancja *on-demand* obsługująca rozszerzenie `pgvector` z minimalnym wolumenem `gp3` ($20\text{ GiB}$) | [Amazon RDS for PostgreSQL Pricing](https://aws.amazon.com/rds/postgresql/pricing/) (2026-10-25) |

---

## 2. Zweryfikowany w kodzie rozmiar rekordów w DynamoDB (`FILE#` i `CHUNK#/FULL#`) oraz S3 JSON

W implementacji `services/gateway/internal/storage/dynamo.go` (linie 16–33 oraz 129–171) brama zapisuje w tabeli DynamoDB `FilesMetadata` **dwa osobne elementy (items)** dla każdego unikalnego obrazu przyjętego do systemu (`PutFileMetadata` oraz `PutChunkFullHash`). Warto zauważyć, że skrót całego pliku (`FULL#<fullHash>`) **nie tworzy trzeciego osobnego rekordu**, lecz stanowi klucz sortujący (`SK`) w rekordzie indeksowanym skrótem pierwszego fragmentu (`PK = CHUNK#<chunkHash>`):

1. **Rekord metadanych pliku (`fileMetadataItem`, `PutFileMetadata`)**:
   - `PK`: `"FILE#" + fileName` ($2\text{ B}$ nazwy + $5\text{ B}$ prefiksu + $L\text{ B}$ nazwy pliku),
   - `SK`: `"META"` ($2\text{ B}$ nazwy + $4\text{ B}$ wartości = $6\text{ B}$),
   - `S3Key`: `"payloads/" + fileName` ($5\text{ B}$ + $9\text{ B}$ + $L\text{ B}$),
   - `Width`, `Height`, `SizeBytes`: trzy atrybuty liczbowe typu `N` ($\approx 34\text{ B}$ łącznie z nazwami pól),
   - `ChunkHash`: $64$ znaki szesnastkowe SHA-256 ($9\text{ B}$ nazwy + $64\text{ B}$ wartości = $73\text{ B}$),
   - `FullHash`: $64$ znaki szesnastkowe SHA-256 ($8\text{ B}$ nazwy + $64\text{ B}$ wartości = $72\text{ B}$),
   - **narzut indeksowy DynamoDB**: $+100\text{ B}$ na każdy element zgodnie z oficjalną dokumentacją AWS ([Item Size Calculations](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/CapacityUnitCalculations.html)).
   - Dla typowej nazwy pliku o długości $L = 23\text{ znaków}$ (np. `img_20261025_000001.jpg`):
     $$\text{Rozmiar}(\texttt{FILE\#}) = 248\text{ B (atrybuty)} + 100\text{ B (narzut DynamoDB)} = \mathbf{348\text{ B}}$$

2. **Rekord dwuetapowego indeksu skrótów (`chunkFullHashItem`, `PutChunkFullHash`)**:
   - `PK`: `"CHUNK#" + chunkHash` ($2\text{ B}$ + $6\text{ B}$ + $64\text{ B}$ = $72\text{ B}$),
   - `SK`: `"FULL#" + fullHash` ($2\text{ B}$ + $5\text{ B}$ + $64\text{ B}$ = $71\text{ B}$),
   - `S3Key`: `"payloads/" + fileName` ($5\text{ B}$ + $9\text{ B}$ + $L\text{ B}$),
   - `FileName`: `fileName` ($8\text{ B}$ + $L\text{ B}$),
   - **narzut indeksowy DynamoDB**: $+100\text{ B}$ na element.
   - Dla $L = 23\text{ znaków}$:
     $$\text{Rozmiar}(\texttt{CHUNK\#/FULL\#}) = 211\text{ B (atrybuty)} + 100\text{ B (narzut DynamoDB)} = \mathbf{311\text{ B}}$$

3. **Łączny stały ślad w DynamoDB na każdy unikalny przechowywany obraz**:
   $$B_{\text{DDB,unikalny}} = 348\text{ B} + 311\text{ B} = \mathbf{659\text{ B}} \quad (\text{oba rekordy mieszczą się znacznie poniżej progu } 1\text{ KB dla 1 WRU i } 4\text{ KB dla 0.5 RRU})$$
   W scenariuszu podmiany gorszego duplikatu (`substituted`, udział $Z$) funkcja `retireReplaced` (`main.go`, linie 173–204) wywołuje `DeleteFileMetadata` oraz `DeleteChunkFullHash` dla starego pliku i zapisuje dwa nowe rekordy dla pliku wyższej jakości — zmiana netto liczby rekordów w DynamoDB wynosi więc $0$. Duplikaty odrzucone ($X$ oraz $Y$) **nie zapisują żadnych rekordów w DynamoDB**.

4. **Raporty JSON w Amazon S3 (`PutMetadataJSON`, `metadata/<fileName>.json`)**:
   - Zgodnie z `main.go` (linie 96–170) brama wykonuje `PutObject` dla pliku `metadata/<fileName>.json` **dla każdego przeanalizowanego żądania** (zarówno przyjętego, jak i odrzuconego):
     - nowy plik (`inserted`): $\mathbf{298\text{ B}}$,
     - odrzucona dokładna kopia (`exact_copy`): $\mathbf{320\text{ B}}$,
     - odrzucony lub podmieniony duplikat semantyczny (`similar`): $\mathbf{344\text{ B}}$,
     - przy podmianie (`substituted`) stary obiekt `metadata/<old>.json` jest usuwany przez `DeleteObject`, więc w koszyku S3 pozostaje tylko nowy raport JSON.

---

## 3. Macierz wywołań API i koszt jednostkowy odrzucenia duplikatu

W aktualnej architekturze (po usunięciu przestarzałego potoku `FileNameMatchPipeline`, z możliwością jego włączenia flagą `--with-name-lookup` w skrypcie) każde przesłane zdjęcie przechodzi przez potok skrótu pierwszego fragmentu (`FirstChunkPipeline.Check`: `Query` po `PK = CHUNK#<sha256>` z `Limit = 1`), a tylko w przypadku trafienia — przez potok pełnego skrótu (`FullHashPipeline.Check`: `GetItem` po `PK = CHUNK#<sha256>, SK = FULL#<sha256>`). Gdy plik nie jest dokładną kopią, brama wywołuje proces `ai_worker` przez gRPC (`ProcessImage`).

### 3.1. Tabela wywołań na 1 plik w zależności od scenariusza

| Scenariusz (udział w strumieniu) | S3 `PutObject` | S3 `HeadObject` | S3 `DeleteObject` | DDB Read (`0.5 RRU`) | DDB Write (`1 WRU`) | Płatne wywołania chmurowe (S3 + DDB) | gRPC `ProcessImage` | Operacje PG (`pgvector`) | Operacje S3 Vectors (`Query` / `Put` / `Delete`) | Koszt API bramy na $1\,000$ plików | Nadwyżka API bramy wzgl. *Baseline* ($1\text{ PUT}$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline (bez narzędzia)** ($100\%$) | $1$ (obraz) | $0$ | $0$ | $0$ | $0$ | **$1$** | $0$ | $0$ | $0 / 0 / 0$ | $\$0.0050000$ | $\$0.0000000$ |
| **1. Nowy plik, zapisany** ($1 - X - Y - Z$) | $2$ (JSON + obraz) | $0$ | $0$ | $1$ (`Query CHUNK#`) | $2$ (`Put FILE#`, `Put CHUNK#`) | **$5$** | $1$ | $3$ (`lock`, `SELECT`, `INSERT`) | $1 / 1 / 0$ | $\$0.0113125$ | $+\$0.0063125$ |
| **2. Dokładna kopia, odrzucona** ($Y$, `FullDuplicate`) | $1$ (tylko JSON) | $0$ | $0$ | $2$ (`Query CHUNK#`, `Get FULL#`) | $0$ | **$3$** | $0$ | $0$ | $0 / 0 / 0$ | $\$0.0051250$ | **$+\$0.0001250$** |
| **3. Podobny, gorszy/równy, odrzucony** ($X$, `SemanticDuplicate`) | $1$ (tylko JSON) | $0$ | $0$ | $1$ (`Query CHUNK#` *miss*) | $0$ | **$2$** | $1$ | $3$ (`lock`, `SELECT`, `FOR UPDATE`) | $1 / 0 / 0$ | $\$0.0050625$ | **$+\$0.0000625$** |
| **4. Podobny, lepszej jakości, podmieniony** ($Z$, `Substituted`) | $2$ (JSON + obraz) | $1$ (`PayloadBytes`) | $2$ (stary obraz + JSON) | $2$ (`Query CHUNK#`, `Get FILE#`) | $4$ (`2 Put`, `2 Delete`) | **$11$** ($9$ płatnych + $2$ darmowe `DELETE`) | $1$ | $4$ (`lock`, `SELECT`, `FOR UPDATE`, `UPDATE`) | $1 / 1 / 1$ | $\$0.0130250$ | $+\$0.0080250$ |

> **Kluczowy wniosek z analizy pojedynczego odrzucenia**:
> - Gdy narzędzie **odrzuca dokładną kopię ($Y$)**, **nie wykonuje** `PutObject` dla pliku graficznego (oszczędza $\$0.000005$), a zamiast tego zapisuje jedynie malutki plik `metadata/<file>.json` (koszt $\$0.000005$). Koszt operacji S3 `PUT` znosi się więc dokładnie do zera, a jedyny dodatkowy koszt API względem *Baseline* to **dwa odczyty z DynamoDB (`Query` + `GetItem`) = $\$0.000000125$ ($0.125\text{ }\mu\$$)** oraz utrzymanie $320\text{ B}$ raportu JSON w S3. Dla pliku o rozmiarze $2.5\text{ MB}$ uniknięty koszt przechowywania w S3 wynosi $\$0.00005615/\text{mies.}$, co oznacza, że **samo wywołanie odrzucające duplikat kryptograficzny zwraca się po $1.63\text{ godziny}$**.
> - Gdy narzędzie **odrzuca duplikat semantyczny ($X$)**, na poziomie bramy (S3 + DynamoDB) dopłata wynosi tylko **jeden odczyt `Query CHUNK#` = $\$0.0000000625$ ($0.0625\text{ }\mu\$$)** (zwraca się po **$0.81\text{ godziny}$** przy lokalnym `pgvector`, lub po $\approx 3.3\text{ godz.}$ przy doliczeniu kosztu zapytania `QueryVectors` w Amazon S3 Vectors).
> - Głównym składnikiem jednorazowego kosztu API narzędzia nie jest więc samo odrzucanie duplikatów, lecz **„podatek od indeksowania” płacony od plików unikalnych ($1 - X - Y - Z$)**, dla których brama wykonuje dodatkowy zapis `metadata/<file>.json` oraz dwa zapisy `PutItem` w DynamoDB ($+\$0.0000063125$ na każdy plik unikalny).

---

## 4. Model matematyczny i wyprowadzenie wzorów na próg rentowności (*break-even*)

Niech:
- $s_{\text{MB}}$ oznacza średni rozmiar przesyłanego obrazu w megabajtach ($\text{MiB}$), skąd liczba plików przypadających na $1\text{ GB}$ ($\text{GiB}$) strumienia wejściowego wynosi:
  $$N_f = \frac{1\,024}{s_{\text{MB}}} \quad \left(\text{dla } s_{\text{MB}} = 2.5\text{ MB}: \quad N_f = 409.6\text{ plików/GB}\right)$$
- $X \in [0, 1)$ oznacza udział odrzuconych duplikatów semantycznych (`SemanticDuplicates`),
- $Y \in [0, 1)$ oznacza udział odrzuconych duplikatów pełnych (`FullDuplicates`),
- $Z \in [0, 1)$ oznacza udział duplikatów semantycznych o wyższej jakości, które podmieniają starszą wersję (`Substituted`, ze średnim przyrostem rozmiaru $\alpha = \Delta s / s_{\text{MB}}$),
- $U = 1 - X - Y - Z$ oznacza udział plików całkowicie nowych (`inserted`).

### 4.1. Zaoszczędzona przestrzeń S3 i współczynnik redukcji $R_s$

Każdy odrzucony plik ($X + Y$) redukuje zajętość koszyka `payloads/` o $100\%$ swojego rozmiaru, natomiast podmiana pliku ($Z$) zastępuje stary obiekt nowym (zmiana netto o $\alpha Z$). Efektywny współczynnik redukcji przestrzeni obrazów w S3 wynosi:
$$R_s = X + Y - \alpha Z \quad \left(\text{dla } X = 0.25,\; Y = 0.25,\; Z = 0: \quad R_s = 0.50\text{, czyli } 50\%\text{ zaoszczędzonego miejsca}\right)$$

### 4.2. Jednorazowy narzut wywołań API na $1\text{ GB}$ wsadu ($I$)

Na każdy $1\text{ GB}$ danych wejściowych ($N_f$ plików):
- **Koszt *Baseline* (bez narzędzia)**:
  $$C_{\text{put},0} = N_f \cdot c_{\text{put}} = 409.6 \times \$0.000005 = \mathbf{\$0.002048 / \text{GB}}$$
- **Koszt wywołań API narzędzia** (S3 + DynamoDB + ewentualne wywołania warstwy wektorowej $C_{\text{vec,api}}$):
  $$C_{\text{api}} = N_f \cdot \Big[ U \cdot c_{\text{new}} + Y \cdot c_{\text{exact}} + X \cdot c_{\text{sim}} + Z \cdot c_{\text{sub}} \Big] + C_{\text{vec,api}}$$
  gdzie jednostkowe koszty bramy z Tabeli 3.1 wynoszą:
  $$c_{\text{new}} = 2c_{\text{put}} + c_{\text{rru}} + 2c_{\text{wru}} = \$11.3125 \times 10^{-6}$$
  $$c_{\text{exact}} = c_{\text{put}} + (1 + h)c_{\text{rru}} = \$5.1250 \times 10^{-6} \quad (\text{gdzie } h = 1 - X/(1-Y)\text{ to trafność prefiksu \texttt{CHUNK\#}})$$
  $$c_{\text{sim}} = c_{\text{put}} + (1 + h_{\text{kol}})c_{\text{rru}} = \$5.0625 \times 10^{-6}$$
  $$c_{\text{sub}} = 2c_{\text{put}} + c_{\text{head}} + 2c_{\text{rru}} + 4c_{\text{wru}} = \$13.0250 \times 10^{-6}$$
- **Jednorazowa dopłata (narzut inwestycyjny API) na $1\text{ GB}$ przetworzonego strumienia ($I$)**:
  $$I = C_{\text{api}} - C_{\text{put},0}$$
  Dla $X = 0.25, Y = 0.25, Z = 0$ oraz lokalnego `pgvector` ($C_{\text{vec,api}} = 0$):
  $$C_{\text{api}} = \$0.003360 / \text{GB}, \qquad I = \$0.003360 - \$0.002048 = \mathbf{\$0.001312 / \text{GB}} \quad (\approx \$1.31\text{ na } 1\,000\text{ GB})$$

### 4.3. Miesięczny koszt stałego utrzymywania DynamoDB, S3 JSON i indeksu wektorowego ($K_{\text{utrz}}$) oraz oszczędność netto ($S$)

Ponieważ **nigdy nie wyłączamy DynamoDB ani S3**, każdy $1\text{ GB}$ przeanalizowanego strumienia pozostawia na stałe:
1. **W Amazon DynamoDB** ($N_f \cdot U$ par rekordów `FILE#` + `CHUNK#/FULL#` o łącznym rozmiarze $659\text{ B}$ na plik unikalny):
   $$K_{\text{DDB}} = \frac{N_f \cdot U \cdot 659\text{ B}}{2^{30}\text{ B/GB}} \cdot C_{\text{DDB}} = 0.00012569\text{ GB} \times \$0.25 = \mathbf{\$0.00003142 / \text{GB-mies.}} \quad (\$0.0314 / \text{mies. na } 1\,000\text{ GB})$$
2. **W Amazon S3 (`metadata/<file>.json`)** ($N_f \cdot [U \cdot 298\text{ B} + Y \cdot 320\text{ B} + (X+Z)\cdot 344\text{ B}]$):
   $$K_{\text{JSON}} = \frac{N_f \cdot \bar{B}_{\text{JSON}}}{2^{30}\text{ B/GB}} \cdot C_{\text{S3}} = 0.00012016\text{ GB} \times \$0.023 = \mathbf{\$0.00000276 / \text{GB-mies.}} \quad (\$0.0028 / \text{mies. na } 1\,000\text{ GB})$$
3. **W indeksie wektorowym** ($N_f \cdot U$ wektorów `512 x float32`):
   - dla lokalnego `pgvector`: $K_{\text{vec,stor}} = \$0.00$,
   - dla **Amazon S3 Vectors** ($2\,092\text{ B/wektor}$ przy $\$0.06/\text{GB-mies.}$): $K_{\text{vec,stor}} = \mathbf{\$0.00002394 / \text{GB-mies.}}$ ($\$0.0239/\text{mies. na } 1\,000\text{ GB}$),
   - dla **Amazon Aurora PostgreSQL** ($\approx 4\,608\text{ B/wektor}$ z krotką i indeksem HNSW przy $\$0.10/\text{GB-mies.}$): $K_{\text{vec,stor}} = \mathbf{\$0.00008789 / \text{GB-mies.}}$ ($\$0.0879/\text{mies. na } 1\,000\text{ GB}$).

Łączny zmienny koszt utrzymania metadanych i indeksów na $1\text{ GB}$ wejścia wynosi:
$$K_{\text{utrz}} = K_{\text{DDB}} + K_{\text{JSON}} + K_{\text{vec,stor}}$$
Natomiast **miesięczna oszczędność netto $S$ na każdym $1\text{ GB}$ przeanalizowanych danych** (po odjęciu stałego utrzymywania DynamoDB, plików JSON w S3 oraz wektorów) to:
$$S = R_s \cdot C_{\text{S3}} - K_{\text{utrz}}$$
Dla $X = 0.25, Y = 0.25, Z = 0$:
- oszczędność brutto na S3 (`payloads/`): $R_s \cdot C_{\text{S3}} = 0.50 \times \$0.023 = \mathbf{\$0.01150000 / \text{GB-mies.}}$,
- koszt utrzymania DynamoDB + S3 JSON (lokalny `pgvector`): $K_{\text{utrz}} = \$0.00003142 + \$0.00000276 = \mathbf{\$0.00003419 / \text{GB-mies.}}$ (zaledwie **$0.30\%$** wygenerowanej oszczędności S3!),
- oszczędność netto: $S = \$0.01150000 - \$0.00003419 = \mathbf{\$0.01146581 / \text{GB-mies.}}$ (czyli **$\$11.47/\text{mies.}$ na każde $1\,000\text{ GB}$**).

---

### 4.4. Równania progu rentowności (*break-even*) dla Wariantu A i Wariantu B

Niech $F \ge 0$ oznacza stały miesięczny koszt utrzymania infrastruktury wektorowej niezależny od liczby plików (np. $F = \$0$ dla lokalnego `pgvector`, S3 Vectors oraz Aurora Serverless v2 z auto-pauzą `0 ACU`; $F = \$13.98/\text{mies.}$ dla RDS `db.t4g.micro`; $F = \$43.80/\text{mies.}$ dla Aurora Serverless v2 `0.5 ACU`).

#### Wariant A — Jednorazowy wsad $V\text{ [GB]}$, brak dalszych zapisów, S3 i DynamoDB działają stale przez $T$ miesięcy

Bilans skumulowanych oszczędności po $T$ miesiącach od załadowania zbioru o objętości $V$:
$$\Delta C_A(V, T) = V \cdot \left( T \cdot S - I \right) - T \cdot F \ge 0$$

Stąd wynikają trzy fundamentalne wzory:
1. **Minimalny wolumen $V_{\min}$, powyżej którego miesięczna oszczędność S3 przewyższa stały koszt utrzymania S3 + DynamoDB + bazy wektorowej** ($\text{miesięczny zysk netto } V \cdot S - F > 0$):
   $$V_{\min} = \frac{F}{S}$$
   - Gdy $F = 0$ (lokalny `pgvector`, S3 Vectors, Aurora `0 ACU`), $V_{\min} = \mathbf{0\text{ GB}}$ — **dla dowolnie małego wolumenu $V > 0$ miesięczny koszt utrzymania S3 + DynamoDB z narzędziem jest niższy niż sam koszt S3 bez narzędzia** (dla $1\,000\text{ GB}$ płacimy miesięcznie **$\$11.53/\text{mies.}$ zamiast $\$23.00/\text{mies.}$**).
   - Gdy $F > 0$ (np. stała instancja RDS lub Aurora bez auto-pauzy), w koszyku S3 musi leżeć co najmniej $V_{\min}$ danych, aby zaoszczędzony co miesiąc czynsz S3 pokrywał stały abonament bazy wektorowej.
2. **Czas zwrotu $T_A(V)$ dla zadanego wolumenu $V > V_{\min}$**:
   $$T_A(V) = \frac{V \cdot I}{V \cdot S - F} \qquad \xrightarrow{F = 0} \qquad T_A = \frac{I}{S}$$
   Dla $F = 0$ czas zwrotu **nie zależy od wolumenu $V$** i wynosi:
   - lokalny `pgvector`: $T_A = \frac{0.001312}{0.011466} = \mathbf{0.1144\text{ mies.}} \approx \mathbf{3.5\text{ dnia}}$,
   - Amazon S3 Vectors (`batch=1`): $T_A = \mathbf{0.64\text{ mies.}} \approx \mathbf{19.5\text{ dnia}}$ (lub **$0.21\text{ mies.} \approx 6.4\text{ dnia}$** przy `batch=60`),
   - Amazon Aurora Serverless v2 (`0 ACU` auto-pauza): $T_A = \mathbf{1.03\text{ mies.}} \approx \mathbf{31.4\text{ dnia}}$.
3. **Wolumen $V_A^*(T)$ wymagany do zwrotu w dokładnie $T$ miesiącach**:
   $$V_A^*(T) = \frac{T \cdot F}{T \cdot S - I}$$

#### Wariant B — Ciągły napływ $v\text{ [GB/mies.]}$ przez $T$ miesięcy (S3 i DynamoDB rosną co miesiąc)

W miesiącu $m \in \{1, \dots, T\}$ w chmurze przechowywane jest już $m \cdot v\text{ GB}$ danych historycznych, a brama przetwarza nową porcję $v\text{ GB}$. Suma jednostek wolumeno-miesięcy po $T$ miesiącach wynosi $\sum_{m=1}^T m = \frac{T(T+1)}{2}$. Bilans skumulowanych oszczędności po $T$ miesiącach ma postać:
$$\Delta C_B(v, T) = v \cdot \left[ \frac{T(T+1)}{2} \cdot S - T \cdot I_{\text{średnie}}(T) \right] - T \cdot F \ge 0$$

Stąd:
1. **Przepływ miesięczny $v_B^*(T)$ wymagany do wyjścia na plus po $T$ miesiącach ciągłego ruchu**:
   $$v_B^*(T) = \frac{T \cdot F}{\frac{T(T+1)}{2} \cdot S - T \cdot I} = \frac{2F}{(T+1)S - 2I}$$
2. **Czas zwrotu $T_B(v)$ przy stałym napływie $v\text{ GB/mies.}$**:
   - dla $F = 0$ system wychodzi na plus już w **pierwszym miesiącu ($T < 1\text{ mies.}$)**: po **$\approx 3.5\text{ dnia}$** dla lokalnego `pgvector` oraz po **$\approx 18.9\text{ dnia}$ ($0.62\text{ mies.}$)** dla Amazon S3 Vectors,
   - dla $F > 0$ (stała instancja bazodanowa) nawet przy małym miesięcznym napływie $v$ rosnące z miesiąca na miesiąc oszczędności na kumulującym się koszyku S3 ($\frac{T(T+1)}{2} v S$) ostatecznie przebijają liniowo rosnący koszt stały $T \cdot F$ po czasie:
     $$T_B(v) = \frac{2(v I + F)}{v S} - 1$$

---

## 5. Porównanie 5 architektur wektorowych (`pgvector` vs. Amazon S3 Vectors vs. Aurora / RDS)

Poniższa tabela zestawia wyniki wygenerowane przez skrypt `finops_breakeven.py` dla domyślnych udziałów duplikatów **$X = 0.25$ (semantyczne), $Y = 0.25$ (pełne), $Z = 0.00$**, średniego rozmiaru pliku **$2.5\text{ MB}$** ($409\,600\text{ plików}$ w $1\,000\text{ GB}$) przy **stale włączonym DynamoDB i S3**.

| Parametr / Metryka FinOps | 1. Lokalny PostgreSQL + `pgvector` (`local`, $F=\$0$) | 2a. Amazon S3 Vectors (`s3vectors`, `batch=1`) | 2b. Amazon S3 Vectors (`s3vectors`, `batch=60`) | 3. Aurora Serverless v2 + `pgvector` (`aurora_pause`, `0 ACU`) | 4. RDS PostgreSQL `db.t4g.micro` (`rds_micro`, Single-AZ) | 5. Aurora Serverless v2 + `pgvector` (`aurora_min`, `0.5 ACU`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Stały abonament mocy obliczeniowej ($F$)** | $\$0.00 / \text{mies.}$ | $\$0.00 / \text{mies.}$ | $\$0.00 / \text{mies.}$ | $\$0.00 / \text{mies.}$ (auto-pauza do `0 ACU`) | $\$13.98 / \text{mies.}$ ($\$11.68$ EC2 + $\$2.30$ `gp3`) | $\$43.80 / \text{mies.}$ ($0.5\text{ ACU} \times 730\text{ h}$) |
| **Koszt API wgrania $1\,000\text{ GB}$ (*Baseline* bez narzędzia)** | $\$2.0480$ | $\$2.0480$ | $\$2.0480$ | $\$2.0480$ | $\$2.0480$ | $\$2.0480$ |
| **Koszt API bramy (S3 + DDB) na $1\,000\text{ GB}$** | $\$3.3600$ | $\$3.3600$ | $\$3.3600$ | $\$3.3600$ | $\$3.3600$ | $\$3.3600$ |
| **Jednorazowy koszt warstwy wektorowej na $1\,000\text{ GB}$** | $\$0.0000$ | $\$6.0046$ (`PutVectors` $\$5.00$ + `Query` $\$1.00$) | $\$1.0879$ (`PutVectors` $\$0.083$ + `Query` $\$1.00$) | $\$10.4243$ ($85.3\text{ ACU-h}$ podczas wsadu + I/O) | $\$0.0000$ (w cenie instancji) | $\$5.3043$ ($\Delta 0.5\text{ ACU}$ podczas wsadu + I/O) |
| **Łączna dopłata jednorazowa nad *Baseline* ($1\,000\text{ GB}$)** | **$+\$1.3120$** | **$+\$7.3166$** | **$+\$2.4000$** | **$+\$11.7363$** | **$+\$1.3120$** | **$+\$6.6163$** |
| **Miesięczny koszt S3 *Baseline* ($1\,000\text{ GB}$, bez narzędzia)** | $\$23.0000 / \text{mies.}$ | $\$23.0000 / \text{mies.}$ | $\$23.0000 / \text{mies.}$ | $\$23.0000 / \text{mies.}$ | $\$23.0000 / \text{mies.}$ | $\$23.0000 / \text{mies.}$ |
| **Miesięczny koszt z narzędziem po wsadzie $1\,000\text{ GB}$**: | **$\$11.5342 / \text{mies.}$** | **$\$11.5581 / \text{mies.}$** | **$\$11.5581 / \text{mies.}$** | **$\$11.6221 / \text{mies.}$** | **$\$25.5142 / \text{mies.}$** | **$\$55.4221 / \text{mies.}$** |
| — w tym S3 (`500 GB` obrazów + `123 MB` JSON) | $\$11.5028$ | $\$11.5028$ | $\$11.5028$ | $\$11.5028$ | $\$11.5028$ | $\$11.5028$ |
| — w tym DynamoDB (`204 800` par `FILE#` + `CHUNK#/FULL#`, `128.7 MB`) | $\$0.0314$ | $\$0.0314$ | $\$0.0314$ | $\$0.0314$ | $\$0.0314$ | $\$0.0314$ |
| — w tym Storage + Compute warstwy wektorowej (`204 800` wektorów) | $\$0.0000$ | $\$0.0239$ (`408.6 MB` w S3 Vectors) | $\$0.0239$ (`408.6 MB` w S3 Vectors) | $\$0.0879$ (`0.88 GB` w Aurora Storage) | $\$13.9800$ (`db.t4g.micro` + `20 GB gp3`) | $\$43.8879$ (`0.5 ACU` + `0.88 GB` Storage) |
| **Miesięczna oszczędność netto na $1\,000\text{ GB}$ (gdy nikt już nic nie rusza)** | **$+\$11.4658 / \text{mies.}$** | **$+\$11.4419 / \text{mies.}$** | **$+\$11.4419 / \text{mies.}$** | **$+\$11.3779 / \text{mies.}$** | $-\$2.5142 / \text{mies.}$ (wymaga $>1\,219\text{ GB}$) | $-\$32.4221 / \text{mies.}$ (wymaga $>3\,850\text{ GB}$) |
| **Wariant A**: Minimalny wolumen $V_{\min}$, by miesięczne utrzymanie było tańsze | **$> 0\text{ GB}$** | **$> 0\text{ GB}$** | **$> 0\text{ GB}$** | **$> 0\text{ GB}$** | **$1\,219.28\text{ GB}$** | **$3\,849.56\text{ GB}$** |
| **Wariant A**: Czas zwrotu $T_A$ dla wsadu $1\,000\text{ GB}$ | **$0.11\text{ mies.}$ ($\approx 3.5\text{ dnia}$)** | **$0.64\text{ mies.}$ ($\approx 19.5\text{ dnia}$)** | **$0.21\text{ mies.}$ ($\approx 6.4\text{ dnia}$)** | **$1.03\text{ mies.}$ ($\approx 31.4\text{ dnia}$)** | Nigdy dla $1\,000\text{ GB}$ (zwraca się dla $\ge 1\,231\text{ GB}$ w 12 mies.) | Nigdy dla $1\,000\text{ GB}$ (zwraca się dla $\ge 4\,046\text{ GB}$ w 12 mies.) |
| **Wariant A**: Bilans netto po $12\text{ mies.}$ dla wsadu $1\,000\text{ GB}$ | **$+\$136.28$** | **$+\$129.99$** | **$+\$134.90$** | **$+\$124.80$** | $-\$31.48$ | $-\$395.68$ |
| **Wariant B**: Czas zwrotu $T_B$ przy ciągłym napływie $100\text{ GB/mies.}$ | **$0.11\text{ mies.}$ ($\approx 3.5\text{ dnia}$)** | **$0.62\text{ mies.}$ ($\approx 18.9\text{ dnia}$)** | **$0.19\text{ mies.}$ ($\approx 5.8\text{ dnia}$)** | **$1.53\text{ mies.}$ ($\approx 46.6\text{ dnia}$)** | **$23.61\text{ mies.}$** (lub $190.9\text{ GB/mies.}$ dla zwrotu w 12 mies.) | **$77.17\text{ mies.}$** (lub $650.4\text{ GB/mies.}$ dla zwrotu w 12 mies.) |
| **Wariant B**: Bilans netto po $12\text{ mies.}$ przy $100\text{ GB/mies.}$ ($1\,200\text{ GB}$ łącznie) | **$+\$87.86$** | **$+\$80.47$** | **$+\$86.37$** | **$+\$74.66$** | $-\$79.90$ | $-\$444.79$ |

### Wnioski architektoniczne z porównania 5 backendów wektorowych

1. **Dlaczego Amazon S3 Vectors (`s3vectors`) deklasuje stale włączone instancje RDS/Aurora przy mniejszych i średnich wolumenach?**
   - W Amazon S3 Vectors płacimy wyłącznie za faktycznie przechowywane wektory ($\$0.06/\text{GB-mies.}$, czyli niespełna **$2.4\text{ centa miesięcznie}$ za $204\,800$ wektorów** odpowiadających $1\,000\text{ GB}$ danych wejściowych) oraz za wywołania `PutVectors` i `QueryVectors`.
   - Ponieważ `PutVectors` nalicza minimum $128\text{ KB}$ na jedno wywołanie API ([PutVectors API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_S3VectorBuckets_PutVectors.html)), zapis pojedynczego wektora `512 x float32` ($2\,092\text{ B}$ z kluczem i metadanymi) bez paczkowania kosztuje $\$0.00002441$ (czyli $\$5.00$ za $204\,800$ nowych obrazów w $1\,000\text{ GB}$). Mimo tego narzutu inwestycja zwraca się już po **$19.5\text{ dnia}$ ($0.64\text{ mies.}$)** w Wariancie A i **$18.9\text{ dnia}$ ($0.62\text{ mies.}$)** w Wariancie B.
   - Zgrupowanie zapisów po $60\text{ wektorów}$ w jednym żądaniu `PutVectors` (`--s3v-batch 60`, $\approx 125.5\text{ KB} \le 128\text{ KB}$) redukuje koszt `PutVectors` $60$-krotnie (z $\$5.00$ do $\$0.083$ na $1\,000\text{ GB}$) i skraca czas zwrotu w S3 Vectors do **$6.4\text{ dnia}$ ($0.21\text{ mies.}$)**.
2. **Aurora PostgreSQL Serverless v2 (`pgvector`) — kluczowa rola auto-pauzy (`MinCapacity = 0 ACU`)**:
   - Włączenie auto-pauzy ([Aurora Serverless v2 Auto-Pause](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html)) sprawia, że w okresach bezczynności płacimy wyłącznie za przestrzeń dyskową Aurora Storage ($\$0.10/\text{GB-mies.}$, czyli $\$0.0879/\text{mies.}$ dla $1\,000\text{ GB}$ wsadu), dzięki czemu system zwraca się po **$1.03\text{ mies.}$** w Wariancie A oraz po **$1.53\text{ mies.}$** w Wariancie B.
   - Pozostawienie klastra Aurora Serverless v2 bez auto-pauzy (`MinCapacity = 0.5 ACU` = $\$43.80/\text{mies.}$) wymaga zgromadzenia w S3 co najmniej **$3\,849.56\text{ GB}$** danych wejściowych ($1\,925\text{ GB}$ zablokowanych duplikatów), aby same miesięczne oszczędności na S3 pokryły stały koszt bazodanowy.
3. **RDS PostgreSQL `db.t4g.micro` (`pgvector`)**:
   - Stały koszt $\$13.98/\text{mies.}$ (instancja `db.t4g.micro` $\$11.68/\text{mies.}$ + minimalne $20\text{ GiB}$ dysku `gp3` $\$2.30/\text{mies.}$) staje się rentowny powyżej **$V_{\min} = 1\,219.28\text{ GB}$** jednorazowego wsadu (zwrot w 12 miesięcy przy $1\,231\text{ GB}$, lub w 1 miesiąc przy $1\,377\text{ GB}$).

---

## 6. Szczegółowe rozbicie operacji i przepływów pieniężnych (dla $1\,000\text{ GB}$ w Wariancie A i $100\text{ GB/mies.}$ w Wariancie B)

### 6.1. Liczba wykonanych operacji i bilans przestrzeni na $1\,000\text{ GB}$ wsadu ($409\,600\text{ plików po }2.5\text{ MB}$, $X=0.25, Y=0.25, Z=0$)

| Kategoria operacji / zasobu | Bez narzędzia (*Baseline*) | Z narzędziem deduplikacji | Różnica ($\text{Narzędzie} - \text{Baseline}$) |
| :--- | :---: | :---: | :---: |
| **Przesłane pliki wejściowe** | $409\,600$ ($1\,000.00\text{ GB}$) | $409\,600$ ($1\,000.00\text{ GB}$) | $0$ plików |
| — w tym zapisane unikalne obrazy (`inserted`, $50\%$) | $409\,600$ ($1\,000.00\text{ GB}$) | $204\,800$ ($500.00\text{ GB}$) | **$-204\,800$ obrazów ($-500.00\text{ GB}$)** |
| — w tym odrzucone dokładne kopie ($Y = 25\%$) | $0$ | $102\,400$ ($250.00\text{ GB}$ zablokowane) | $+102\,400$ odrzuconych |
| — w tym odrzucone duplikaty semantyczne ($X = 25\%$) | $0$ | $102\,400$ ($250.00\text{ GB}$ zablokowane) | $+102\,400$ odrzuconych |
| **Wywołania S3 `PutObject`** | $409\,600$ | $614\,400$ ($204\,800$ obrazów + $409\,600$ JSON) | $+204\,800$ ($\$1.0240$) |
| **Wywołania DynamoDB Read (`Query` + `GetItem`)** | $0$ | $512\,000$ ($409\,600$ `Query` + $102\,400$ `GetItem`) | $+512\,000$ ($\$0.0320$) |
| **Wywołania DynamoDB Write (`PutItem` `FILE#` + `CHUNK#`)** | $0$ | $409\,600$ ($204\,800 \times 2$ rekordy) | $+409\,600$ ($\$0.2560$) |
| **Wywołania gRPC `ProcessImage` (CLIP `ViT-B/32`)** | $0$ | $307\,200$ (pominięto $102\,400$ kopii $Y$!) | $+307\,200$ |
| **Zajętość S3 (`payloads/` + `metadata/*.json`)** | $1\,000.00\text{ GB}$ | $500.1202\text{ GB}$ ($500\text{ GB}$ + $123.05\text{ MB}$ JSON) | **$-499.8798\text{ GB}$ (oszczędność netto)** |
| **Zajętość DynamoDB (`FILE#` + `CHUNK#/FULL#`)** | $0.00\text{ MB}$ | $128.71\text{ MB}$ ($409\,600$ rekordów, $659\text{ B/obraz}$) | $+128.71\text{ MB}$ ($\$0.0314/\text{mies.}$) |

### 6.2. Przebieg miesięczny — Wariant A ($1\,000\text{ GB}$ wgrane w Miesiącu 1, potem $0\text{ GB/mies.}$, S3 i DynamoDB stale włączone)

| Miesiąc | Nowy wsad | Koszt miesięczny *Baseline* (S3) | Koszt miesięczny *Narzędzie* (`local`) | Koszt miesięczny *Narzędzie* (`s3vectors`, `batch=1`) | Koszt miesięczny *Narzędzie* (`aurora_pause`) | Skumulowana oszczędność netto (`local`) | Skumulowana oszczędność netto (`s3vectors`) | Skumulowana oszczędność netto (`aurora_pause`) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1** | $1\,000\text{ GB}$ | $\$25.05$ | $\$14.89$ | $\$20.92$ | $\$25.41$ | **$+\$10.15$** *(zwrot w 3.5 d)* | **$+\$4.13$** *(zwrot w 19.5 d)* | $-\$0.36$ |
| **2** | $0\text{ GB}$ | $\$23.00$ | $\$11.53$ | $\$11.56$ | $\$11.62$ | **$+\$21.62$** | **$+\$15.57$** | **$+\$11.02$** *(zwrot w 31.4 d)* |
| **3** | $0\text{ GB}$ | $\$23.00$ | $\$11.53$ | $\$11.56$ | $\$11.62$ | $+\$33.09$ | $+\$27.01$ | $+\$22.40$ |
| **6** | $0\text{ GB}$ | $\$23.00$ | $\$11.53$ | $\$11.56$ | $\$11.62$ | $+\$67.48$ | $+\$61.34$ | $+\$56.53$ |
| **12** | $0\text{ GB}$ | $\$23.00$ | $\$11.53$ | $\$11.56$ | $\$11.62$ | **$+\$136.28$** | **$+\$129.99$** | **$+\$124.80$** |

### 6.3. Przebieg miesięczny — Wariant B (ciągły napływ $100\text{ GB/mies.}$ przez 12 miesięcy, S3 i DynamoDB stale rosną)

| Miesiąc | Skumulowany wsad | Zaoszczędzone S3 (`payloads/`) | Rozmiar tabeli DynamoDB | Koszt miesięczny *Baseline* | Koszt miesięczny *Narzędzie* (`local`) | Koszt miesięczny *Narzędzie* (`s3vectors`) | Skumulowana oszczędność netto (`local`) | Skumulowana oszczędność netto (`s3vectors`) | Skumulowana oszczędność netto (`aurora_pause`) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1** | $100\text{ GB}$ | $50\text{ GB}$ | $12.87\text{ MB}$ | $\$2.50$ | $\$1.49$ | $\$2.07$ | **$+\$1.02$** | **$+\$0.44$** | $-\$0.04$ |
| **2** | $200\text{ GB}$ | $100\text{ GB}$ | $25.74\text{ MB}$ | $\$4.80$ | $\$2.64$ | $\$3.23$ | $+\$3.18$ | $+\$2.01$ | **$+\$1.00$** |
| **3** | $300\text{ GB}$ | $150\text{ GB}$ | $38.61\text{ MB}$ | $\$7.10$ | $\$3.80$ | $\$4.39$ | $+\$6.49$ | $+\$4.72$ | $+\$3.17$ |
| **6** | $600\text{ GB}$ | $300\text{ GB}$ | $77.23\text{ MB}$ | $\$14.00$ | $\$7.26$ | $\$7.88$ | $+\$23.29$ | $+\$19.72$ | $+\$16.49$ |
| **12** | $1\,200\text{ GB}$ | $600\text{ GB}$ | $154.45\text{ MB}$ | $\$27.80$ | $\$14.18$ | $\$14.89$ | **$+\$87.86$** | **$+\$80.47$** | **$+\$74.66$** |

---

## 7. Analiza wrażliwości na odsetek duplikatów ($X + Y$)

Tabela prezentuje minimalny czas zwrotu $T_A$ (dla $F = 0$) oraz minimalne wolumeny progu rentowności dla RDS `db.t4g.micro` ($F = \$13.98/\text{mies.}$) przy różnym nasyceniu strumienia duplikatami ($X = Y = R/2$, $Z = 0$):

| Łączny udział duplikatów $R = X + Y$ | Skład ($X$ semantyczne + $Y$ pełne) | Oszczędność netto $S$ (`local`, na $1\,000\text{ GB}$) | Czas zwrotu $T_A$ (`local`, $F=\$0$) | Czas zwrotu $T_A$ (`s3vectors`, `batch=1`) | Czas zwrotu $T_A$ (`s3vectors`, `batch=60`) | Minimalny wolumen $V_{\min}$ dla RDS `db.t4g.micro` ($\$13.98/\text{mies.}$) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$5.85\%$** *(próg 1 mies. dla `local`)* | $2.925\% + 2.925\%$ | $\$1.283 / \text{mies.}$ | **$1.00\text{ mies.}$ ($30.4\text{ d}$)** | $8.51\text{ mies.}$ | $2.33\text{ mies.}$ | $10\,896\text{ GB}$ |
| **$10\%$** | $5\% + 5\%$ | $\$2.240 / \text{mies.}$ | **$0.83\text{ mies.}$ ($25.2\text{ d}$)** | $4.73\text{ mies.}$ | $1.37\text{ mies.}$ | $6\,241\text{ GB}$ |
| **$20\%$** | $10\% + 10\%$ | $\$4.547 / \text{mies.}$ | **$0.37\text{ mies.}$ ($11.3\text{ d}$)** | $2.15\text{ mies.}$ | $0.64\text{ mies.}$ | $3\,075\text{ GB}$ |
| **$30\%$** | $15\% + 15\%$ | $\$6.853 / \text{mies.}$ | **$0.22\text{ mies.}$ ($6.7\text{ d}$)** | $1.30\text{ mies.}$ | $0.40\text{ mies.}$ | $2\,040\text{ GB}$ |
| **$50\%$** *(scenariusz bazowy)* | $25\% + 25\%$ | $\$11.466 / \text{mies.}$ | **$0.11\text{ mies.}$ ($3.5\text{ d}$)** | **$0.64\text{ mies.}$ ($19.5\text{ d}$)** | **$0.21\text{ mies.}$ ($6.4\text{ d}$)** | **$1\,219\text{ GB}$** |
| **$70\%$** | $35\% + 35\%$ | $\$16.079 / \text{mies.}$ | **$0.06\text{ mies.}$ ($1.8\text{ d}$)** | **$0.32\text{ mies.}$ ($9.8\text{ d}$)** | **$0.11\text{ mies.}$ ($3.4\text{ d}$)** | **$869\text{ GB}$** |

---

## 8. Instrukcja uruchomienia skryptu `finops_breakeven.py`

Skrypt `/home/user/workspace/rozdzialy_2_3_korekta/finops_breakeven.py` pozwala swobodnie stroić udziały `SEMANTIC_DUPLICATES_X`, `FULL_DUPLICATES_Y`, `SUBSTITUTED_SIMILAR_Z` bezpośrednio w nagłówku pliku (linie 36–63) lub za pomocą argumentów wiersza poleceń:

```bash
# Domyślne uruchomienie (X=0.25, Y=0.25, Z=0.00, 1000 GB jednorazowo / 100 GB/mies. ciągle, porównanie wszystkich 5 backendów):
python3 finops_breakeven.py

# Wybór konkretnego backendu (np. Amazon S3 Vectors) z paczkowaniem 60 wektorów w PutVectors:
python3 finops_breakeven.py --backend s3vectors --s3v-batch 60

# Symulacja z częściową podmianą duplikatów semantycznych na lepszą jakość (X=0.20, Y=0.25, Z=0.05):
python3 finops_breakeven.py --x 0.20 --y 0.25 --z 0.05 --volume-once 2000 --volume-monthly 250
```

---

## 9. Bibliografia cennikowa i dokumentacyjna (z datami weryfikacji)

1. **Amazon Web Services**, *Amazon S3 Pricing (S3 Standard Storage, Requests & Data Retrievals, and Amazon S3 Vectors)*, oficjalny cennik AWS (region `us-east-1`): Storage $\$0.023/\text{GB-mies.}$ (pierwsze $50\text{ TB}$), `PUT/COPY/POST/LIST` $\$0.005/1\,000\text{ żądań}$, `GET/SELECT/HEAD` $\$0.0004/1\,000\text{ żądań}$, `DELETE` $\$0.00$; S3 Vectors Storage $\$0.06/\text{GB-mies.}$, `PutVectors` $\$0.20/\text{GB}$ (min. $128\text{ KB}$), `QueryVectors` $\$2.50/1\text{ mln}$ zapytań + $\$0.004/\text{TB}$ (do $100\text{ tys.}$ wektorów), `DeleteVectors` $\$0.055/1\,000\text{ żądań}$. URL: [https://aws.amazon.com/s3/pricing/](https://aws.amazon.com/s3/pricing/) — **data dostępu i weryfikacji stawek: 2026-10-25**.
2. **Amazon Web Services**, *Amazon S3 API Reference: PutObject, HeadObject, DeleteObject*, oficjalna dokumentacja techniczna operacji obiektowych S3. URL: [https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html) oraz [https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html) — **data dostępu: 2026-10-25**.
3. **Amazon Web Services**, *Amazon S3 Vectors API Reference: Actions (`PutVectors`, `QueryVectors`, `DeleteVectors`)*, oficjalna dokumentacja techniczna koszyków wektorowych S3. URL: [https://docs.aws.amazon.com/AmazonS3/latest/API/API_Operations_Amazon_S3_Vectors.html](https://docs.aws.amazon.com/AmazonS3/latest/API/API_Operations_Amazon_S3_Vectors.html) oraz [https://docs.aws.amazon.com/AmazonS3/latest/API/API_S3VectorBuckets_PutVectors.html](https://docs.aws.amazon.com/AmazonS3/latest/API/API_S3VectorBuckets_PutVectors.html) — **data dostępu: 2026-10-25**.
4. **Amazon Web Services**, *Amazon DynamoDB Pricing (On-Demand Read/Write Request Units and Table Storage)*, oficjalny cennik AWS (region `us-east-1`): zapis $\$0.625/1\text{ mln WRU}$, odczyt $\$0.125/1\text{ mln RRU}$ ($0.5\text{ RRU}$ dla odczytu *eventually consistent* do $4\text{ KB}$, tj. $\$0.0625/1\text{ mln}$ żądań), przechowywanie danych $\$0.25/\text{GB-mies.}$ po przekroczeniu $25\text{ GB}$ AWS Free Tier. URL: [https://aws.amazon.com/dynamodb/pricing/](https://aws.amazon.com/dynamodb/pricing/) — **data dostępu i weryfikacji stawek: 2026-10-25**.
5. **Amazon Web Services**, *Amazon DynamoDB Developer Guide: Read/Write Capacity Mode and Item Size Calculations*, oficjalna specyfikacja wyliczania rozmiaru rekordów DynamoDB (suma bajtów atrybutów $+ 100\text{ B}$ narzutu indeksowego na element) oraz jednostek RRU/WRU. URL: [https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/read-write-operations.html](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/read-write-operations.html) oraz [https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/CapacityUnitCalculations.html](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/CapacityUnitCalculations.html) — **data dostępu: 2026-10-25**.
6. **Amazon Web Services**, *Amazon Aurora Pricing (Aurora Serverless v2 & Aurora Storage and I/O)*, oficjalny cennik AWS (region `us-east-1`): Aurora Standard $\$0.12/\text{ACU-godz.}$, Aurora Storage $\$0.10/\text{GB-mies.}$, operacje I/O $\$0.20/1\text{ mln żądań}$. URL: [https://aws.amazon.com/rds/aurora/pricing/](https://aws.amazon.com/rds/aurora/pricing/) — **data dostępu i weryfikacji stawek: 2026-10-25**.
7. **Amazon Web Services**, *Amazon Aurora User Guide: Scaling to 0 ACUs with automatic pause and resume for Aurora Serverless v2* oraz *Extension versions for Aurora PostgreSQL (`pgvector`)*, dokumentacja techniczna skalowania do $0\text{ ACU}$ i obsługi indeksów wektorowych `pgvector`. URL: [https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html) oraz [https://docs.aws.amazon.com/AmazonRDS/latest/AuroraPostgreSQLReleaseNotes/AuroraPostgreSQL.Extensions.html](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraPostgreSQLReleaseNotes/AuroraPostgreSQL.Extensions.html) — **data dostępu: 2026-10-25**.
8. **Amazon Web Services**, *Amazon RDS for PostgreSQL Pricing (On-Demand Instance `db.t4g.micro` & General Purpose SSD `gp3` Storage)*, oficjalny cennik AWS (region `us-east-1`): instancja Single-AZ `db.t4g.micro` $\$0.016/\text{godz.}$ ($\$11.68/\text{mies.}$ przy $730\text{ godz.}$), pamięć masowa `gp3` $\$0.115/\text{GB-mies.}$ (min. $20\text{ GiB} = \$2.30/\text{mies.}$). URL: [https://aws.amazon.com/rds/postgresql/pricing/](https://aws.amazon.com/rds/postgresql/pricing/) — **data dostępu i weryfikacji stawek: 2026-10-25**.
