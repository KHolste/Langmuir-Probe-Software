# Langmuir Probe Measurement — Documentation outline

Companion outline to `LangmuirMeasure_Documentation.docx`.  Contains
the same section hierarchy in a lightweight markdown form so the
structure can be grepped, diffed, and embedded inside a GitHub
README without opening Word.

## A. Short overview / Kurzüberblick
- A.1 English short overview
- A.2 Deutscher Kurzüberblick

## B. Full user manual (English)
- B.1 Purpose of the software
- B.2 Supported measurement modes
- B.3 Hardware used
- B.4 Installation prerequisites
- B.5 Installing the application
- B.6 First startup
- B.7 Connecting the instruments
- B.8 Walking through the GUI
- B.9 How Single / Double / Triple analysis works
- B.10 Interpreting the output
- B.11 Warnings and common failure messages
- B.12 Saving, loading, and sidecar files
- B.13 Practical troubleshooting
- B.14 Recommended operator workflow

## C. Vollständiges Benutzerhandbuch (Deutsch)
- C.1 Zweck der Software
- C.2 Unterstützte Messmodi
- C.3 Verwendete Hardware
- C.4 Installationsvoraussetzungen
- C.5 Installation der Anwendung
- C.6 Erster Start
- C.7 Geräte verbinden
- C.8 Die Bedienoberfläche im Detail
- C.9 Funktionsweise der Analysen
- C.10 Ergebnisse interpretieren
- C.11 Warnungen und typische Fehlermeldungen
- C.12 Speichern, Laden, Sidecar-Dateien
- C.13 Praktische Fehlersuche
- C.14 Empfohlener Arbeitsablauf

## D. Developer / GitHub documentation
- D.1 Project purpose (EN + DE)
- D.2 Architecture overview / Architekturüberblick
- D.3 Key modules (table)
- D.4 Build + test + installer flow
- D.5 Runtime prerequisites
- D.6 Project conventions
- D.7 Where to start for future development
- D.8 Known limitations / future work

## E. Glossary / Glossar
Bilingual side-by-side table for:
Langmuir probe · Single / Double / Triple probe · floating potential ·
plasma potential · electron temperature · ion saturation current ·
electron density / ion density · confidence interval · compliance /
clipping · bootstrap CI · VISA · GPIB · RS232 · sidecar file ·
fit status · NPLC · Bohm velocity · Huber loss.

## F. Contact / Kontakt
Issues and patches via the GitHub issue tracker.  Lab-operator
questions at JLU-IPI: I. Physikalisches Institut.

---

## How to regenerate the DOCX
```bat
python docs\build_documentation.py
```

The script is self-contained — no network access.  Adjust the prose
inside `docs/build_documentation.py` and re-run to produce an
updated `LangmuirMeasure_Documentation.docx`.
