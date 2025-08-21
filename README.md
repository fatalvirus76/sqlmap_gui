# 🛡️ SQLMap GUI

**SQLMap GUI** is a graphical user interface (GUI) for the popular tool **sqlmap**, used to detect and exploit SQL injection vulnerabilities.  
The purpose of this program is to make sqlmap easier to use by presenting its many commands and flags in an organized, clickable format.

---

## ✨ Features

- **Graphical Interface**  
  - Tabbed window that organizes sqlmap’s hundreds of options into logical categories such as *Target, Request, Injection, Enumeration*, etc.  
  - Eliminates the need to memorize long command-line flags.  

- **Command Builder**  
  - Build complete sqlmap commands by filling in fields, checking boxes, and selecting from dropdowns.  

- **Scan Management**  
  - Run scans directly inside the program and view results in real time on a dedicated *output* tab.  
  - Option to launch scans in an external terminal (CMD, Terminal, xterm, etc.) – useful for interactive sessions.  
  - Stop ongoing scans with a single button click.  

- **Process Handling**  
  - Each scan runs in its own thread → the GUI remains responsive even during long scans.  

- **Settings & Profiles**  
  - Save and load configurations for easy reuse of complex settings.  
  - Automatically detects the sqlmap installation on your system.  

- **User-Friendly Tools**  
  - **Copy Command** button to easily paste the generated command into a terminal.  
  - Reset all options to their default values.  
  - Support for multiple visual themes.  
  - Built-in access to sqlmap’s help (`sqlmap -hh`).  

---

## 📌 Summary (≤350 chars)
A graphical interface for sqlmap that simplifies vulnerability testing.  
It organizes sqlmap commands into tabs, lets you build and run scans visually, manages processes in the background, and allows you to save and load configurations.  

---

## ⚙️ Requirements

This program requires both a Python library and sqlmap itself:  

### Python Library
Install via `pip`:  
```bash
pip install PySide6



# 🛡️ SQLMap GUI

**SQLMap GUI** är ett grafiskt användargränssnitt (GUI) för det populära verktyget **sqlmap**, som används för att upptäcka och utnyttja SQL-injektionssårbarheter.  
Syftet är att göra sqlmap mer lättanvänt genom att presentera dess många kommandon och flaggor i ett organiserat och klickbart format.

---

## ✨ Programmets Funktioner

- **Grafiskt Gränssnitt**  
  - Flikbaserat fönster som organiserar sqlmaps hundratals alternativ i kategorier: *Target, Request, Injection, Enumeration* m.fl.  
  - Eliminerar behovet av att memorera kommandoradsflaggor.  

- **Kommandokonstruktion**  
  - Bygg kompletta sqlmap-kommandon genom att fylla i fält, kryssa i rutor och välja från listor.  

- **Skanningshantering**  
  - Kör skanningar direkt i programmet och se resultat i realtid på en separat *output*-flik.  
  - Alternativt starta skanningar i ett externt terminalfönster (CMD, Terminal, xterm etc.) – praktiskt för interaktiva sessioner.  
  - Stoppa pågående skanningar med en knapptryckning.  

- **Processhantering**  
  - Varje skanning körs i en egen tråd → GUI:t förblir responsivt även under långa analyser.  

- **Inställningar & Profiler**  
  - Spara och ladda konfigurationer för att återanvända komplexa inställningar.  
  - Programmet kan automatiskt hitta sqlmap på systemet.  

- **Användarvänlighet**  
  - **Kopiera Kommando**-knapp för att enkelt klistra in i terminal.  
  - Återställ alla alternativ till standardvärden.  
  - Stöd för visuella teman.  
  - Inbyggd funktion för att visa sqlmaps hjälptext (`sqlmap -hh`).  

---

## 📌 Sammanfattning (≤350 tecken)
Ett grafiskt gränssnitt för sqlmap som förenklar sårbarhetstestning.  
Organiserar sqlmap-kommandon i flikar, låter användaren bygga och köra skanningar visuellt, hanterar processer i bakgrunden och gör det enkelt att spara och ladda konfigurationer.  

---

## ⚙️ Beroenden att Installera

För att köra detta program krävs både Python-bibliotek och sqlmap:  

### Python-bibliotek
Installera via `pip`:  
```bash
pip install PySide6
