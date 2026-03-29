# TokenPak Architecture Diagrams

This directory contains editable architecture diagrams for TokenPak.

## Files

### `architecture.excalidraw`
Editable architecture diagram showing the full system layout:
- Client applications on the left
- TokenPak proxy with internal components in the center
- Multiple LLM providers on the right
- Data flow arrows with labels

**How to edit:**
1. Open the file at https://excalidraw.com
2. File → Open → Select `architecture.excalidraw`
3. Edit and re-export as PNG (File → Export → Download as PNG)

### Rendered PNG (static reference)
When exporting from Excalidraw:
- **Format:** PNG, 1200px+ width, transparent background
- **Filename:** `architecture.png`
- **Location:** This directory (`docs/assets/`)

## Component Diagram

See `/docs/architecture.md` for the embedded Mermaid component diagram showing internal module relationships.

## Request Flow Diagram

See `/docs/architecture.md` for the sequence diagram showing a complete request flow from application through proxy to provider and back.
