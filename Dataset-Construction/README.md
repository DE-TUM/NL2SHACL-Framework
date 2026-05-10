# Dataset Construction

This folder contains the tools for constructing paired NL-SHACL datasets for the NL2SHACL task. Starting from raw SHACL shape files and ontologies, the pipeline produces human-reviewed records, each consisting of a reference SHACL shapes graph, a natural language description, and an ontology snippet.

The pipeline runs across three modules in sequence:

1. **[Data Preprocessor](Data-Preprocessor/)** — extracts shape fragments from raw SHACL files, checks their quality, augments them with ontology metadata, and converts the results into a structured dataset format.
2. **[Description Generator](Description-Generator/)** — generates natural language descriptions for each shape fragment using the Gemini API.
3. **[Description Reviewer](Description-Reviewer/)** — provides a graphical interface for human annotators to review, edit, and approve the generated descriptions.