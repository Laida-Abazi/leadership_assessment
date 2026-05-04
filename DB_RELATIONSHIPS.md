# Database Relationships

This diagram shows the current backend data model around assessments, candidate access, answers, and analysis.

## ER Diagram

```mermaid
erDiagram
    USERS {
        int id PK
    }

    JOB_REQUIREMENTS {
        int id PK
        string job_id
    }

    JOB_REQUIREMENT_PROFILES {
        int id PK
        int job_requirements_id FK
    }

    ASSESSMENTS {
        int id PK
        int user_id FK
        int job_requirements_id FK
        string assessment_type_code
        string assessment_version
    }

    ASSESSMENT_ITEMS {
        int id PK
        int assessment_id FK
        string item_key
    }

    ASSESSMENT_ANSWERS {
        int id PK
        int assessment_id FK
        int assessment_item_id FK
        string item_key
    }

    RESPONSES {
        int id PK
        int assessment_id FK
    }

    RESPONSE_SEGMENTS {
        int id PK
        int assessment_id FK
        string response_type
    }

    RESPONSE_SIGNALS {
        int id PK
        int assessment_id FK
        int response_segment_id FK
    }

    ANALYSIS {
        int id PK
        int assessment_id FK
        int job_requirements_id FK
        int responses_id FK
    }

    PREDICTIONS {
        int id PK
        int analysis_id FK
    }

    ASSESSMENT_RESULTS {
        int id PK
        int assessment_id FK
    }

    ASSESSMENT_ACCESS_LINKS {
        int id PK
        int assessment_id FK
        int created_by_user_id FK
    }

    ASSESSMENT_CANDIDATES {
        int id PK
        int assessment_id FK
        int access_link_id FK
        string email
    }

    ASSESSMENT_CONTEXT_EMBEDDINGS {
        int id PK
        int assessment_id FK
        int job_requirements_id FK
    }

    USERS ||--o{ ASSESSMENTS : owns
    USERS ||--o{ ASSESSMENT_ACCESS_LINKS : issues

    JOB_REQUIREMENTS ||--o{ ASSESSMENTS : defines
    JOB_REQUIREMENTS ||--o{ JOB_REQUIREMENT_PROFILES : profiles
    JOB_REQUIREMENTS ||--o{ ANALYSIS : informs
    JOB_REQUIREMENTS ||--o{ ASSESSMENT_CONTEXT_EMBEDDINGS : indexed_for

    ASSESSMENTS ||--o{ ASSESSMENT_ITEMS : contains
    ASSESSMENTS ||--o{ ASSESSMENT_ANSWERS : stores
    ASSESSMENTS ||--|| RESPONSES : has_legacy_row
    ASSESSMENTS ||--o{ RESPONSE_SEGMENTS : produces
    ASSESSMENTS ||--o{ RESPONSE_SIGNALS : aggregates
    ASSESSMENTS ||--o{ ANALYSIS : analyzed_into
    ASSESSMENTS ||--|| ASSESSMENT_RESULTS : summarized_as
    ASSESSMENTS ||--o{ ASSESSMENT_ACCESS_LINKS : shared_with
    ASSESSMENTS ||--o{ ASSESSMENT_CANDIDATES : used_by
    ASSESSMENTS ||--o{ ASSESSMENT_CONTEXT_EMBEDDINGS : embedded_as

    ASSESSMENT_ITEMS ||--o{ ASSESSMENT_ANSWERS : answered_by
    RESPONSES ||--o{ ANALYSIS : source_for
    RESPONSE_SEGMENTS ||--o{ RESPONSE_SIGNALS : yields
    ANALYSIS ||--|| PREDICTIONS : predicts
    ASSESSMENT_ACCESS_LINKS ||--o| ASSESSMENT_CANDIDATES : registers
```

## Candidate-Scoped Flow

The analysis pipeline is now intended to be centered on `candidate_id` for interview data, while `assessment_id` remains the shared assessment template/root.

The important chain is:

```mermaid
flowchart LR
    A[assessments]
    B[assessment_access_links]
    C[assessment_candidates]
    D[assessment_answers candidate_id]
    E[responses candidate_id]
    F[response_segments candidate_id]
    G[response_signals candidate_id]
    H[analysis candidate_id]
    I[predictions]
    J[assessment_results candidate_id]

    A --> B
    A --> C
    C --> D
    C --> E
    C --> F
    C --> G
    C --> H
    C --> J
    H --> I
    B --> C
```

What this means in practice:

- multiple candidates can exist for one `assessment`
- each candidate should write answers, segments, signals, analysis, and final result rows into their own `candidate_id` scope
- admin listing endpoints can now return one row per candidate without reusing another candidate's analysis
- the remaining shared tables, like `assessments`, `assessment_items`, and `job_requirements`, still describe the reusable assessment definition rather than a specific interview attempt
