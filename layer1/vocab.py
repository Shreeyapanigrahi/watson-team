"""Seed vocabularies for Layer 1.

Used by (a) the data generator, (b) NER-style matching of brands and indications
during metadata extraction, (c) the quality scorer's clinical vocabulary, and
(d) the taxonomy store. Every brand and drug name is FICTIONAL (the brief requires
the corpus to be clearly synthetic).
"""

THERAPEUTIC_AREAS = ["Oncology", "Cardiology", "Neurology", "Immunology"]

# retention_days: Section 4.8 gives HCP survey 730, KOL interview 365, competitive
# landscape 1825. The brief gives no value for the other three types; 1095 days
# (3 years) is OUR assumption, flagged in design_decisions.md.
STUDY_TYPES = {
    "HCP Perception Survey": {
        "retention_days": 730,
        "keywords": ["perception survey", "hcp survey", "respondents", "fieldwork", "survey of"],
    },
    "Competitive Landscape Report": {
        "retention_days": 1825,
        "keywords": ["competitive landscape", "market share", "second-line", "competitor"],
    },
    "Indication Deep Dive": {
        "retention_days": 1095,
        "keywords": ["deep dive", "unmet need", "prevalence", "epidemiology", "standard of care"],
    },
    "Market Access Insight": {
        "retention_days": 1095,
        "keywords": ["market access", "reimbursement", "payer", "health technology", "pricing"],
    },
    "KOL Interview Summary": {
        "retention_days": 365,
        "keywords": ["kol interview", "interviewed", "key opinion leader", "interview summary"],
    },
    "Therapy Area Landscape": {
        "retention_days": 1095,
        "keywords": ["therapy area landscape", "pipeline", "late-stage assets", "therapy area"],
    },
}

SENSITIVITY_LEVELS = ["PUBLIC", "INTERNAL", "RESTRICTED", "CONFIDENTIAL"]
SENSITIVITY_RANK = {s: i for i, s in enumerate(SENSITIVITY_LEVELS)}

# region code -> countries. "UK" sits in EU because the brief lists it among the
# EU countries (DE, FR, IT, ES, UK).
GEOGRAPHIES = {
    "DE": {"name": "Germany", "region": "EU", "payer_type": "Statutory health insurance", "regulatory_body": "BfArM / EMA"},
    "FR": {"name": "France", "region": "EU", "payer_type": "National health insurance", "regulatory_body": "ANSM / EMA"},
    "IT": {"name": "Italy", "region": "EU", "payer_type": "Regional national health service", "regulatory_body": "AIFA / EMA"},
    "ES": {"name": "Spain", "region": "EU", "payer_type": "Regional national health service", "regulatory_body": "AEMPS / EMA"},
    "UK": {"name": "United Kingdom", "region": "EU", "payer_type": "National health service", "regulatory_body": "MHRA"},
    "US": {"name": "United States", "region": "US", "payer_type": "Mixed commercial and government", "regulatory_body": "FDA"},
    "JP": {"name": "Japan", "region": "APAC", "payer_type": "National health insurance", "regulatory_body": "PMDA"},
}
REGIONS = ["EU", "US", "APAC"]
GEOGRAPHY_VALUES = REGIONS + ["GLOBAL"]   # document-level geography

# Aliases the matcher also accepts (lower-case match).
INDICATIONS = {
    "non-small cell lung cancer": {"ta": "Oncology", "aliases": ["NSCLC"]},
    "EGFR-mutant NSCLC": {"ta": "Oncology", "aliases": ["EGFR-mutant non-small cell lung cancer"]},
    "KRAS G12C-mutant NSCLC": {"ta": "Oncology", "aliases": []},
    "small cell lung cancer": {"ta": "Oncology", "aliases": ["SCLC"]},
    "HER2-positive breast cancer": {"ta": "Oncology", "aliases": ["HER2+ breast cancer"]},
    "triple-negative breast cancer": {"ta": "Oncology", "aliases": ["TNBC"]},
    "melanoma": {"ta": "Oncology", "aliases": []},
    "renal cell carcinoma": {"ta": "Oncology", "aliases": ["RCC"]},
    "heart failure with reduced ejection fraction": {"ta": "Cardiology", "aliases": ["HFrEF"]},
    "heart failure with preserved ejection fraction": {"ta": "Cardiology", "aliases": ["HFpEF"]},
    "atrial fibrillation": {"ta": "Cardiology", "aliases": []},
    "multiple sclerosis": {"ta": "Neurology", "aliases": []},
    "migraine": {"ta": "Neurology", "aliases": []},
    "Alzheimer's disease": {"ta": "Neurology", "aliases": ["Alzheimer disease"]},
    "plaque psoriasis": {"ta": "Immunology", "aliases": []},
    "psoriatic arthritis": {"ta": "Immunology", "aliases": []},
    "rheumatoid arthritis": {"ta": "Immunology", "aliases": []},
    "atopic dermatitis": {"ta": "Immunology", "aliases": []},
}

# name, generic (fictional INN), class, ATC (class prefix + fictional suffix), mechanism, company, TA, indications
_B = [
    ("Keytrava", "pemvrolimab", "PD-1 inhibitor", "L01FF91", "Blocks the PD-1 receptor to restore T-cell activity", "Aldenmoor Pharma", "Oncology",
     ["non-small cell lung cancer", "melanoma", "renal cell carcinoma"]),
    ("Opdivra", "nivoltrimab", "PD-1 inhibitor", "L01FF92", "Blocks the PD-1 receptor to restore T-cell activity", "Corvane Therapeutics", "Oncology",
     ["non-small cell lung cancer", "melanoma"]),
    ("Tagrisa", "oselvertinib", "EGFR tyrosine kinase inhibitor", "L01EB91", "Inhibits mutant EGFR signalling", "Helvara Biosciences", "Oncology",
     ["EGFR-mutant NSCLC", "non-small cell lung cancer"]),
    ("Lumakro", "sotavacib", "KRAS G12C inhibitor", "L01XX91", "Locks mutant KRAS G12C in its inactive state", "Norquist Health", "Oncology",
     ["KRAS G12C-mutant NSCLC", "non-small cell lung cancer"]),
    ("Enhertra", "trasdelutecan", "HER2 antibody-drug conjugate", "L01FD91", "Delivers a topoisomerase inhibitor to HER2-expressing cells", "Aldenmoor Pharma", "Oncology",
     ["HER2-positive breast cancer"]),
    ("Trodelvia", "sacitodrinan", "TROP2 antibody-drug conjugate", "L01FX91", "Delivers a cytotoxic payload to TROP2-expressing cells", "Helvara Biosciences", "Oncology",
     ["triple-negative breast cancer"]),
    ("Entrestra", "sacuvartan", "Angiotensin receptor-neprilysin inhibitor", "C09DX91", "Combines neprilysin inhibition with angiotensin receptor blockade", "Corvane Therapeutics", "Cardiology",
     ["heart failure with reduced ejection fraction"]),
    ("Jardiva", "empagrazin", "SGLT2 inhibitor", "A10BK91", "Inhibits renal glucose and sodium reabsorption", "Norquist Health", "Cardiology",
     ["heart failure with reduced ejection fraction", "heart failure with preserved ejection fraction"]),
    ("Verquvia", "verciguat", "Soluble guanylate cyclase stimulator", "C01EB91", "Stimulates soluble guanylate cyclase to improve vascular tone", "Aldenmoor Pharma", "Cardiology",
     ["heart failure with reduced ejection fraction"]),
    ("Eliquex", "apixaprin", "Factor Xa inhibitor", "B01AF91", "Inhibits factor Xa to prevent clot formation", "Helvara Biosciences", "Cardiology",
     ["atrial fibrillation"]),
    ("Ocrevia", "ocrelizimab", "Anti-CD20 antibody", "L04AA91", "Depletes CD20-positive B cells", "Corvane Therapeutics", "Neurology",
     ["multiple sclerosis"]),
    ("Aimovia", "eremunab", "CGRP receptor antibody", "N02CD91", "Blocks the CGRP receptor to prevent migraine attacks", "Norquist Health", "Neurology",
     ["migraine"]),
    ("Leqembra", "lecanemimab", "Anti-amyloid antibody", "N06DX91", "Clears amyloid-beta plaques", "Aldenmoor Pharma", "Neurology",
     ["Alzheimer's disease"]),
    ("Humivex", "adalimutab", "TNF inhibitor", "L04AB91", "Neutralises tumour necrosis factor alpha", "Helvara Biosciences", "Immunology",
     ["plaque psoriasis", "rheumatoid arthritis", "psoriatic arthritis"]),
    ("Adalibra", "adalimutab", "TNF inhibitor (biosimilar of Humivex)", "L04AB91", "Neutralises tumour necrosis factor alpha", "Corvane Therapeutics", "Immunology",
     ["plaque psoriasis", "rheumatoid arthritis"]),
    ("Cosentex", "secukimab", "IL-17A inhibitor", "L04AC91", "Neutralises interleukin-17A", "Corvane Therapeutics", "Immunology",
     ["plaque psoriasis", "psoriatic arthritis"]),
    ("Skyrixa", "risankimab", "IL-23 inhibitor", "L04AC92", "Blocks the p19 subunit of interleukin-23", "Norquist Health", "Immunology",
     ["plaque psoriasis", "psoriatic arthritis"]),
    ("Dupixa", "dupilumib", "IL-4/IL-13 receptor antagonist", "D11AH91", "Blocks the shared IL-4 receptor alpha subunit", "Aldenmoor Pharma", "Immunology",
     ["atopic dermatitis"]),
]
BRANDS = {
    b[0]: {"drug": b[1], "drug_class": b[2], "atc": b[3], "mechanism": b[4],
           "company": b[5], "ta": b[6], "indications": b[7]}
    for b in _B
}

ADVERSE_EVENTS = {
    "Oncology": ["immune-related adverse events", "fatigue", "rash", "diarrhoea", "infusion reactions", "pneumonitis"],
    "Cardiology": ["hypotension", "hyperkalaemia", "renal function decline", "dizziness", "bleeding events"],
    "Neurology": ["injection-site reactions", "infusion reactions", "headache", "constipation", "ARIA imaging findings"],
    "Immunology": ["injection-site reactions", "upper respiratory infections", "conjunctivitis", "nasopharyngitis"],
}

# Clinical vocabulary for the quality scorer's text-richness signal.
CLINICAL_TERMS = sorted({
    "efficacy", "tolerability", "safety", "adverse events", "adverse event", "progression-free survival",
    "overall survival", "objective response rate", "hazard ratio", "placebo-controlled", "randomised",
    "randomized", "biomarker", "adherence", "polypharmacy", "dose", "dosing", "dosage", "endpoint",
    "endpoints", "cohort", "double-blind", "open-label", "phase 3", "phase iii", "phase 2", "phase ii",
    "first-line", "second-line", "third-line", "refractory", "relapse", "remission", "mortality",
    "morbidity", "hospitalisation", "hospitalization", "quality of life", "biosimilar", "monotherapy",
    "combination therapy", "chemotherapy", "immunotherapy", "targeted therapy", "mutation", "metastatic",
    "unresectable", "adjuvant", "neoadjuvant", "ejection fraction", "natriuretic peptide", "anticoagulation",
    "stroke", "relapse rate", "disability progression", "lesion", "infusion", "subcutaneous", "intravenous",
    "pharmacokinetics", "immunogenicity", "antibody", "inhibitor", "receptor", "cytokine", "interleukin",
    "tumour", "tumor", "oncologist", "cardiologist", "neurologist", "rheumatologist", "dermatologist",
    "prescribing", "reimbursement", "health technology assessment", "formulary", "payer", "unmet need",
    "standard of care", "guideline", "real-world evidence", "patient-reported outcomes", "treatment burden",
    "persistence", "discontinuation", "contraindication", "comorbidity", "comorbidities", "prevalence",
    "incidence", "epidemiology", "pipeline", "mechanism of action", "titration", "maintenance therapy",
    "response rate", "sample size", "respondents", "survey", "sentiment", "market share", "launch",
    "indication", "therapeutic area", "clinical trial", "clinical benefit", "benefit-risk",
})

PERSON_FIRST = ["Elena", "Marc", "Sofia", "Henrik", "Amira", "Lukas", "Ingrid", "Tomas", "Nadia", "Pavel",
                "Clara", "Jonas", "Maya", "Felix", "Irene", "Oscar"]
PERSON_LAST = ["Rossi", "Lindqvist", "Okafor", "Brandt", "Moreau", "Kowalski", "Haddad", "Verhoeven",
               "Castellano", "Nakamura", "Albrecht", "Fontaine", "Petrov", "Ellison", "Marchetti", "Duarte"]
ANALYST_NAMES = ["Priya Venkat", "Daniel Hartwell", "Mei Tanaka", "Sofia Albrecht", "Rahul Desai",
                 "Greta Lindholm", "Omar Faruk", "Hannah Weiss"]
