\documentclass[a4paper,9pt]{extarticle}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{lmodern,microtype,multicol,enumitem,tabularx,array,geometry,hyperref,needspace,pifont}
\geometry{top=14mm,bottom=13mm,left=15mm,right=15mm}
\hypersetup{hidelinks,pdfauthor={Lab screening},pdftitle={${TITLE_PDF}}}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{3pt}
\setlength{\columnsep}{7mm}
\setlist[enumerate]{leftmargin=13pt,itemsep=2pt,topsep=2pt,parsep=0pt}
\newcommand{\sect}[1]{\par\addvspace{8pt}\noindent{\bfseries\fontsize{12}{14}\selectfont #1}\par\nobreak\vspace{-2pt}}
\newcommand{\src}[1]{\textsuperscript{[#1]}}
\begin{document}
{\fontsize{19}{24}\selectfont\bfseries ${TITLE}}\par
{${SUBTITLE}}\par
\vspace{5pt}
\begin{multicols*}{2}
\raggedcolumns
\fontsize{10.5}{12.8}\selectfont
\sect{Criteria}
{\footnotesize\begin{tabularx}{\linewidth}{@{}>{\raggedright\arraybackslash}X >{\raggedleft\arraybackslash}p{54pt}@{}}
${CRITERIA_ROWS}
\end{tabularx}}
\sect{Institute}
${INSTITUTE_SUMMARY}\par
\sect{Working language}
${WORKING_LANGUAGE}\par
\sect{Research directions}
${RESEARCH_DIRECTIONS}
\sect{Methods}
${METHOD_SUMMARY}\par
\sect{Research ownership}
${OWNERSHIP}
\sect{Alumni outcomes}
\begin{tabularx}{\linewidth}{@{}>{\raggedright\arraybackslash}p{23mm}>{\raggedright\arraybackslash}X@{}}
${POSTDOC_ROWS}
\end{tabularx}
\sect{Postdoc fit}
${FIT_PARAGRAPHS}
\sect{RIE2030 alignment}
${RIE2030_ALIGNMENT}\par
\end{multicols*}
\newpage
{\bfseries Evidence appendix}\par
{\fontsize{16}{19}\selectfont\bfseries Experimental methods and provenance}\par
{${APPENDIX_SUBTITLE}}\par
\vspace{6pt}
\textbf{How to read the count.} Each selected original paper counts once per method family.\par
\vspace{3pt}
\footnotesize
\renewcommand{\arraystretch}{1.08}
\sloppy
\begin{tabularx}{\dimexpr\textwidth-3pt\relax}{@{}>{\raggedright\arraybackslash}p{41mm}>{\centering\arraybackslash}p{17mm}>{\centering\arraybackslash}p{26mm}>{\raggedright\arraybackslash}X@{}}
\bfseries Experimental family & \bfseries Count & \bfseries Papers & \bfseries Example and training caveat\\\hline
${METHOD_ROWS}
\hline
\end{tabularx}
\vspace{3pt}
{\footnotesize ${METHOD_NOTES}}\par
\sect{PhD-qualified alumni and outcomes}
{\footnotesize ${ALUMNI_SCOPE}}\par
\footnotesize
\begin{tabularx}{\textwidth}{@{}p{32mm}p{29mm}>{\raggedright\arraybackslash}X@{}}
\bfseries Person & \bfseries Connection & \bfseries Documented next step\\\hline
${ALUMNI_ROWS}
\hline
\end{tabularx}
\sect{Selected paper denominator}
\setlength{\multicolsep}{2pt}
\begin{multicols}{2}
\scriptsize
${REFERENCES}
\end{multicols}
\sect{Other evidence}
\begin{multicols}{2}
\scriptsize
${OTHER_REFERENCES}
\end{multicols}
{\footnotesize ${FIGURE_NOTE}}\par
\end{document}
