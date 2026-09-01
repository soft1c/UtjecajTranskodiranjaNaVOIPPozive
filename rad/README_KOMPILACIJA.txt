KOMPILACIJA RADA
================

1. Iz korijenskog direktorija osvježiti LaTeX rezultate:

   ./venv/bin/python scripts/analysis/generate_latex_results.py

2. Kompajlirati rad:

   cd rad
   pdflatex diplomski.tex
   bibtex diplomski
   pdflatex diplomski.tex
   pdflatex diplomski.tex

Rad koristi standardni bibliografski stil plainnat. Službeni fakultetski
znak fakulteta nije uključen; dodati ga samo prema važećem fakultetskom predlošku.
