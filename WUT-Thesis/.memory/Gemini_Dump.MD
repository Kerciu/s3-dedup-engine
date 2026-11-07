# Dokumentacja Projektowa: Projekt inteligentnej bramy (Gateway) oraz narzędzia CLI do semantycznej deduplikacji danych w systemach Cloud Storage

## 1. Stack Technologiczny i Środowisko Pracy (Pisanie inżynierki)
Zamiast przestarzałych edytorów, środowisko pracy opiera się na integracji nowoczesnych narzędzi AI i lokalnej kompilacji:
*   **Edytor:** Visual Studio Code (lub Cursor / Roo Code).
*   **LaTeX:** Rozszerzenie LaTeX Workshop kompilujące w tle (wymaga lokalnego TeX Live lub MiKTeX). Zoptymalizowane plikiem `.vscode/settings.json` pod szablon Politechniki Warszawskiej (biber).
*   **AI do składni i kodu:** GitHub Copilot (darmowy pakiet studencki).
*   **AI do redakcji tekstu:** Wtyczka Continue.dev + darmowe API Gemini 1.5 Pro (duże okno kontekstowe, idealne do utrzymania spójności pracy).
*   **Research literatury:** Perplexity AI (tryb Academic) oraz Google NotebookLM (do czytania paczek plików PDF i generowania przypisów).

---

## 2. Biznesowy Przypadek Użycia (Use Case) i FinOps
Sam tekst jest zbyt tani w przechowywaniu (S3 kosztuje ok. 0.023 USD za 1 GB), aby deduplikacja wyłącznie plików `.txt` miała uzasadnienie ekonomiczne. System zyskuje potężną wartość biznesową w scenariuszu **zgłoszeń awarii IT (Support Tickets / Bug Reports)**.

*   **Problem:** Masowa awaria powoduje napływ setek zgłoszeń (np. 1000 ticketów). Treść zgłoszeń (tekst) różni się słowami, ale sens jest ten sam. Każdy ticket zawiera **ciężkie załączniki** (zrzuty pamięci ~300 MB, logi ~5 MB, wideo ~30 MB). Chmura przyjmuje gigabajty powielonych danych binarnych.
*   **Rozwiązanie (Soft-Deduplication):** Gateway analizuje tekst. Jeśli wykryje duplikat semantyczny (to samo zdarzenie opisane inaczej), system odrzuca fizyczny zapis wielomegabajtowego załącznika na S3, zachowując jedynie mały plik JSON z metadanymi i wskaźnikiem na pierwsze zgłoszenie.

---

## 3. Architektura Systemu i Potok Danych (Pipeline)
Architektura została rozdzielona na szybki filtr w Go oraz analityczny mikroserwis w Pythonie.

### Krok 1: Odsiew syntaktyczny (Aplikacja CLI w Go)
1. Użytkownik wskazuje plik/ticket do wgrania.
2. Go CLI wykonuje błyskawiczny algorytm kryptograficzny (np. SHA-256) na pliku lub używa Rabin-Karp Chunking.
3. Odpytuje **Amazon DynamoDB** w czasie $O(1)$.
4. Jeśli hash istnieje $\rightarrow$ **Kopia 1:1. Zrzut (Odrzucenie) załącznika na poziomie CLI.**

### Krok 2: Odsiew semantyczny (Mikroserwis w Pythonie)
1. Jeśli plik jest nowy bitowo, Go wysyła tekst ticketa do Pythona.
2. Python uruchamia potok sztucznej inteligencji, wykorzystując model NLP (np. z HuggingFace).
3. Łączy się z **S3 Vector Bucket**, gdzie znajduje się zindeksowany graf wektorów (np. biblioteka FAISS) oraz plik `mapping.json`.
4. Wylicza miarę *Cosine Similarity* (Podobieństwo Kosinusowe).

### Krok 3: Hybrid Search i Kalibracja
W oparciu o najnowsze badania skuteczności modeli AI na zbiorach Bugzilla, czyste modele oparte o wektory radzą sobie gorzej z "brudnym" tekstem technicznym (logi, stack trace) niż klasyczne algorytmy.
*   **Algorytm Hybrydowy:** System łączy tradycyjne wyszukiwanie leksykalne (**BM25**) z wyszukiwaniem semantycznym (model **mxbai-embed-large-v1**). Wyniki są łączone za pomocą *Reciprocal Rank Fusion (RRF)*.
*   **Próg odcięcia (Threshold):** Nie istnieje ustalony na sztywno próg $\theta=0.90$. Wartość ta jest wyznaczana empirycznie (np. kalibrowana na 0.73) na zbiorze walidacyjnym w celu maksymalizacji metryki F1.

### Krok 4: Zapis na S3 i Information Gain
*   **Nowy ticket:** Tekst i załącznik binarne wędrują na S3 (S3 Raw Bucket). Wektory i hashe uaktualniają bazę.
*   **Duplikat semantyczny:** Uruchamiana jest logika *Information Gain* (wyciąganie encji: kodów błędów, długości). Jeśli nowy ticket jest gorszy/wtórny, CLI w Go odrzuca payload binarny z pamięci RAM. Na S3 ląduje tylko malutki JSON z doczepionymi **Amazon S3 Object Metadata** (np. `x-amz-meta-is-duplicate: true`).

---

## 4. Testy i Symulacja (Metodologia Badawcza)
Z powodu obostrzeń RODO publiczne zbiory (BugHub, Mozilla Core) nie udostępniają zrzutów pamięci ani załączników.

**Symulacja Hybrydowa (FinOps):**
Algorytm testujący (Benchmark Runner) operuje na prawdziwych tekstach zgłoszeń technicznych, ale fizyczny rozmiar załączników generuje syntetycznie przy użyciu **rozkładu logarytmiczno-normalnego** (log-normal distribution), odzwierciedlając warunki korporacyjne.

**Profil prawdopodobieństwa załączników w symulacji:**
*   Brak: 40% (0 MB)
*   Zrzut ekranu: 25% (Mediana: 0.5 MB)
*   Plik Log: 20% (Mediana: 5.0 MB)
*   Wideo: 10% (Mediana: 30.0 MB)
*   Memory Dump: 5% (Mediana: 300.0 MB)

**Metryki sukcesu i porażki:**
*   **True Positives (Zaoszczędzona przestrzeń S3):** Prawidłowo wykryty duplikat $\rightarrow$ zablokowano transfer ciężkiego pliku.
*   **False Positives (Utracone dane):** AI błędnie uznało oryginalny błąd za duplikat $\rightarrow$ skasowano (nieodwracalnie!) kluczowy zrzut pamięci (Największe ryzyko systemu!).

---

## 5. Najważniejsze Gotowe Fragmenty Tekstu (Do wklejenia w Teorię)

**O progu Cosine Similarity:**
> "Powszechnie przyjęty w analizie NLP próg odcięcia (threshold) dla miary cosinusowej na poziomie 0.85-0.90 okazuje się nieskuteczny w kontekście danych technicznych, takich jak zgłoszenia błędów (Bug Reports). Wynika to z faktu, iż obecność szumu syntaktycznego – zrzutów pamięci, logów serwerowych czy śladów stosu (stack traces) – zaburza dystrybucję wektorów. Zgodnie z wynikami pomiarów na zbiorach syntetycznych i rzeczywistych, optymalny próg odcięcia podlega procesowi ciągłej kalibracji (F1-score optimization) na zbiorze walidacyjnym."

**O relacji Obiekt-Załącznik w S3:**
> "W projektowanym rozwiązaniu przyjęto, że logiczna jednostka zgłoszenia (Ticket) mapowana jest na usługę Amazon S3 jako zbiór odrębnych obiektów: deskryptor formatu JSON oraz obiekty binarne stanowiące załączniki (tzw. Payload). W przypadku pozytywnej detekcji duplikatu semantycznego przez moduł AI, brama (Gateway) realizuje wzorzec Soft-Deduplication. Polega on na fizycznym odrzuceniu obiektów binarnych duplikatu, przy jednoczesnym zachowaniu deskryptora JSON. Powiązanie z oryginalnym zgłoszeniem realizowane jest bezstanowo, z wykorzystaniem natywnych metadanych usługi S3 (Object User-Defined Metadata, np. poprzez nagłówek x-amz-meta-duplicate-of)."

**O braku rzeczywistych załączników w zbiorze badawczym:**
> "Z uwagi na politykę prywatności (RODO) oraz ograniczenia pojemnościowe otwartych zbiorów danych, publicznie dostępne zbiory zgłoszeń błędów (np. baza Mozilla Core ze zbioru BugHub) nie zawierają fizycznych załączników (...). Aby wiarygodnie zbadać opłacalność ekonomiczną systemu (FinOps), w środowisku testowym zastosowano podejście hybrydowe. Skuteczność deduplikacji semantycznej (F1-score) mierzono na rzeczywistym tekście technicznym. Z kolei warstwę danych binarnych zasymulowano, przypisując każdemu wejściowemu zgłoszeniu pseudolosowy wektor wagowy reprezentujący wielkość załącznika, używając do tego statystycznego rozkładu logarytmiczno-normalnego."