# Annexure-1 — Proposed Solution (Summary)

*iDEX DISC 14 · Challenge 82 · Indian Coast Guard*
*(To be reproduced on the applicant's letterhead, if available.)*

---

## Applicant details

| # | Field | Entry |
|---|---|---|
| 1 | Applicant Name | Eshan Ghose |
| 2 | Startup / MSME Name *(NA for Individual Innovator)* | **[FILL — enter registered entity name, or "NA (Individual Innovator)"]** |
| 3 | Challenge Title | Integration of AI/ML in Coastal Surveillance Network (CSN) Software — DISC 14, Challenge 82, Indian Coast Guard **[FILL — confirm exact title as printed on the iDEX portal]** |
| 4 | Project Duration (in Months) | 12 months |
| 5 | Contact & Email Id | **[FILL — phone]** · eshanghose06@gmail.com |

---

## 1. Brief Summary of the proposed Solution *(≤ 250 words)*

A CSN watchkeeper sees position and kinematics and nothing else. Deciding a
track is suspicious is a human judgement that degrades with fatigue, and the
reasoning is never written down.

**Maritime ISR** is a working prototype. It fuses AIS, coastal radar, satellite
SAR, sanctions and ownership registries and unstructured arrival paperwork into
one ranked list of Vessels of Interest — each carrying a score that decomposes
exactly into named factors, a plain-English reason an officer can read aloud,
and a citable source for every fact.

Five of the challenge's six areas already exist as measured code. Vessel type
and activity are inferred from **motion alone**, so radar-only and AIS tracks
get the same answer (90% coarse-class accuracy, held-out hulls). AIS static data
is checked for internal contradiction. Forward projection is route-aware (median
2.9 nm at three hours). A document reader turns PDF, scanned fax, Word and
spreadsheet PANS into per-field-cited evidence (85.7% field recall over 381
documents, zero unread). An electro-optical loop cues station cameras by global
assignment and alerts when the image contradicts the transponder.

Three rules make it trustworthy rather than merely clever. Every record carries
its source and the code version that produced it. Every check returns
*contradiction*, *clear*, or *not checkable* — "we could not check" is an answer,
never silence. Precision is preferred to recall, because an officer who sees
three false alarms stops opening the fourth.

All figures are measured on a synthetic corpus with injected ground truth;
re-measurement on ICG data is a project deliverable.

---

## 2. Key Technology(s) Used *(not more than 6 in total)*

| # | Technology | Role in the solution |
|---|---|---|
| 1 | **Uber H3 hierarchical hexagonal spatial index** (res 7 ≈ 5 km, res 9 ≈ 170 m) | Every located record — AIS fix, radar contact, SAR detection, scene footprint, camera arc — is stamped with the same cell at ingest, turning sensor-to-sensor correlation from a runtime geometry problem into a hash join. |
| 2 | **DuckDB over columnar Parquet** (zero-server analytical lakehouse) | Raw / normalised / derived layers with an immutable raw tier; whole-fleet analytical queries with no database server to operate on a shore station. |
| 3 | **Global assignment: Jonker-Volgenant / Hungarian solver + Kalman track filtering** | Radar-to-AIS association and EO camera cueing are both solved as one optimal assignment per scene or per slot, never greedy nearest-neighbour — which manufactures phantom contacts and starves cameras. |
| 4 | **Motion-only classifiers (gradient-boosted trees on kinematic features) with confusion-matrix-derived class vocabularies** | Vessel type, activity and vessel-to-vessel interaction inferred from track geometry alone, so the same model serves radar and AIS; the published class list is derived from measured separability rather than declared. |
| 5 | **Multi-format document extraction — PDF text layer, Tesseract OCR, DOCX/XLSX parsers — behind one format-blind extractor, with retrieval-grounded question answering** | Turns PANS attachments and the e-PANS portal feed into one structured record shape with per-field provenance; answers are assembled from retrieved rows, so no fact can be invented. |
| 6 | **ESA SNAP via pyroSAR (`gpt` XML graphs) with CFAR + CNN ship detection on Sentinel-1 SAR** | Detects vessels that are not broadcasting at all, day or night, through cloud — the sensor of last resort behind the dark-vessel verdict. |

*(≈ 50 key words: H3 spatial indexing · DuckDB · Parquet · Hungarian assignment ·
Kalman filter · gradient boosting · motion features · confusion-matrix vocabulary ·
OCR · Tesseract · retrieval-grounded NLP · CFAR · CNN · Sentinel-1 SAR · pyroSAR ·
ESA SNAP · FastAPI · React · MapLibre GL · provenance envelope.)*

---

## 3. Deliverable(s)

| Sr No | Deliverable Name | Brief Description |
|---|---|---|
| 1 | **Fused maritime picture and correlation core** | One source-agnostic engine that ingests coastal radar, AIS (dynamic and static), satellite SAR and registries into a canonical schema on a shared H3 index, and correlates radar-to-AIS by global assignment. Every new feed enters as a connector; the fusion core is never modified for a source. |
| 2 | **AI-based MDA Assistant (challenge 3.6)** | A ranked Vessel-of-Interest list for the watchkeeper. Each subject carries a suspicion score that decomposes *exactly* into its contributing factors, the evidence behind each factor, a plain-English narrative, and a grounded question-answering interface that retrieves rather than generates. |
| 3 | **Radar-only classification module (challenge 3.1)** | Vessel type, activity and vessel-to-vessel interaction (company, shadowing, converging-and-holding, transfer pattern) inferred from kinematics alone, with an honest published class vocabulary and per-call confidence. Runs identically on radar and AIS tracks. |
| 4 | **AIS integrity and predictive track module (challenge 3.4)** | Authenticity checks on AIS static data (IMO check digit, MMSI/flag consistency, registry agreement, declared voyage against actual track), activity classification, route-aware forward projection with an uncertainty cone, and per-area behavioural baselines learned from local history. |
| 5 | **PANS ingestion and risk-fusion application (challenge 3.5)** | Readers for PDF, scanned PDF, DOCX and XLSX arrival notifications and for the e-PANS electronic feed, all producing one record shape through one extractor; per-field provenance and confidence; resolution to a hull that refuses rather than guesses; rules that compare the declared voyage against the observed track. |
| 6 | **Automated EO capture and mismatch loop (challenge 3.2)** | Camera cueing without operator intervention as a global assignment over cameras × tracks, image tagged to the originating track as evidence, classification against a maintained library behind a swappable interface, and an alert when the image contradicts the declared type or identity. |
| 7 | **Multilingual VHF ASR & NLP module (challenge 3.3)** | Speech-to-text over recorded VHF channels for the principal coastal-state languages, normalised to searchable English text, keyword and entity extraction, and linkage of an utterance to the track it most likely came from. *(This is the one area not yet prototyped — see Annexure-2 §3.)* |
| 8 | **Operator surface, incident report and evaluation harness** | Map, Watch, Radar, Vessels and Graph views; one-click incident report with the full evidence chain; and a permanent evaluation harness that re-measures precision, recall and per-rule outcomes on every model change, logged per code version. |

---

## 4. Proposed Timeline(s) *(in months)*

| Phase | Months | Work | Exit criterion |
|---|---|---|---|
| **P1 — Deployment and data foundation** | M1 – M3 | Provision the deployment host; land live AIS, the ICG radar feed and SAR scenes through the existing connectors; stand up raw/normalised/derived storage with the provenance envelope; run the evaluation harness against ICG data for the first time. | The pipeline runs unattended on ICG infrastructure for 14 consecutive days; every landed row carries source, timestamp and code version. |
| **P2 — Radar and AIS analytics on real data (3.1, 3.4)** | M4 – M6 | Re-train and re-measure the motion-only type/activity/interaction models on real radar and AIS tracks; re-derive the published class vocabulary from the *measured* confusion matrix; measure the AIS static-data authenticity checks, which cannot be measured on synthetic identities; fit per-area baselines on real local history. | Coarse-class accuracy and per-rule precision reported on held-out real hulls, with the class vocabulary derived rather than declared. |
| **P3 — PANS and EO on real feeds (3.5, 3.2)** | M7 – M9 | Add readers for the actual agency letterheads and the e-PANS National Logistics Portal (Marine) feed; measure field recall per issuing house; integrate a live EO head at one station, replacing the simulated capture source behind the existing interface; build the image library from captured imagery. | ≥ 90% field recall across all participating agencies; end-to-end automatic capture, tagging and mismatch alert demonstrated on a live camera at one station. |
| **P4 — VHF ASR/NLP, integration and trial (3.3, 3.6)** | M10 – M12 | Build and train the multilingual VHF ASR/NLP module and wire it into the assistant as a sixth evidence family; integrate all six areas into the ranked MDA assistant; user trial with watchkeepers; precision tuning against reviewed dispositions; documentation and handover. | Field trial at a ROC/ROS: ≥ 70% of alerts survive watchkeeper review, with measured reduction in time-to-decision per investigated track. |

---

*Prepared from the Maritime ISR repository. Every performance figure quoted above is
measured on a deterministic synthetic corpus with injected ground truth and is
labelled as such in Annexure-2; no figure in this document is a measurement on
operational Indian Coast Guard data.*
