"""Service taxonomy: what text counts as evidence for each service.

Patterns run on *normalised* text (lowercase, punctuation replaced by spaces,
camelCase split), so 'C-Section' -> 'c section', '24/7' -> '24 7', 'cardiacSurgery'
-> 'cardiac surgery'.

- strong:  terms that directly name the service (e.g. 'hemodialysis', 'dialysis unit').
- support: terms that make the service plausible but don't prove it
           (e.g. 'nephrology' for dialysis). Support-only evidence is capped at "Indirect".
- urgency: controls how fast distance decays (emergency care must be close).
- tier:    'tertiary' services get a plausibility check against facility size/type.
- nfhs:    keywords used to pick relevant NFHS-5 district indicators for context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Service:
    id: str
    label: str
    aliases: tuple[str, ...]
    strong: tuple[str, ...]
    support: tuple[str, ...] = ()
    urgency: str = "urgent"  # emergency | urgent | elective
    tier: str = "secondary"  # primary | secondary | tertiary
    nfhs: tuple[str, ...] = ()
    advice: str = ""
    _compiled: dict = field(default_factory=dict, compare=False, repr=False)

    def strong_re(self) -> re.Pattern:
        if "strong" not in self._compiled:
            self._compiled["strong"] = re.compile("|".join(f"(?:{p})" for p in self.strong))
        return self._compiled["strong"]

    def support_re(self) -> re.Pattern | None:
        if not self.support:
            return None
        if "support" not in self._compiled:
            self._compiled["support"] = re.compile("|".join(f"(?:{p})" for p in self.support))
        return self._compiled["support"]


# Distance half-life (km) by urgency: proximity score halves every N km.
HALF_LIFE_KM = {"emergency": 20.0, "urgent": 40.0, "elective": 80.0}

SERVICES: list[Service] = [
    Service(
        "dialysis", "Dialysis (kidney)",
        ("dialysis", "haemodialysis", "hemodialysis", "kidney failure", "renal replacement"),
        strong=(r"\b(?:haemo|hemo|peritoneal )?dialy[sz]is\b", r"\bhaemodialysis\b", r"\bhemodialysis\b",
                r"\bdialy[sz]ers?\b", r"\bcapd\b", r"\bcrrt\b", r"\bdialysis (?:unit|machine|centre|center)s?\b"),
        support=(r"\bnephrolog", r"\bkidney (?:care|disease|clinic|transplant)", r"\brenal (?:care|unit|failure)"),
        urgency="urgent", tier="secondary",
        nfhs=("blood sugar", "blood pressure", "hypertension", "diabetes"),
        advice="Dialysis is usually scheduled several times a week: confirm slot availability and whether they accept walk-in or emergency dialysis.",
    ),
    Service(
        "emergency_csection", "Emergency C-section / obstetric emergency",
        ("c-section", "c section", "caesarean", "cesarean", "emergency delivery", "high-risk delivery",
         "high risk pregnancy", "obstetric emergency", "lscs"),
        strong=(r"\bc section", r"\bcaesar(?:e)?an", r"\bcesar(?:e)?an", r"\bcaeserean", r"\blscs\b",
                r"\bemergency obstetric", r"\b[bc]emonc\b", r"\bhigh risk (?:pregnan|deliver|obstetric)"),
        support=(r"\bobstetric", r"\bgyn(?:a)?ecolog", r"\blabou?r (?:room|ward)", r"\bmaternity\b",
                 r"\bdeliver(?:y|ies)\b", r"\bblood bank\b", r"\boperation theat(?:re|er)\b"),
        urgency="emergency", tier="secondary",
        nfhs=("caesarean", "institutional birth", "antenatal", "anc", "births attended"),
        advice="Call ahead so the team can prepare: ask whether an obstetrician, anaesthetist and blood are available right now.",
    ),
    Service(
        "nicu", "NICU (newborn intensive care)",
        ("nicu", "neonatal icu", "newborn icu", "sncu", "premature baby", "neonatal intensive care"),
        strong=(r"\bnicu\b", r"\bneo ?natal intensive", r"\bneonatal (?:icu|critical)", r"\bsncu\b",
                r"\bnewborn (?:intensive|icu|care unit)", r"\blevel (?:ii|iii|2|3) (?:nicu|nursery|neonatal)"),
        support=(r"\bneonatolog", r"\bincubators?\b", r"\bphototherapy\b", r"\bradiant warmers?\b",
                 r"\bpicu\b", r"\bpa?ediatric (?:icu|intensive)"),
        urgency="emergency", tier="tertiary",
        nfhs=("institutional birth", "breastfed", "births attended", "home birth"),
        advice="Ask whether a NICU bed and a neonatal ventilator are free before transferring the baby.",
    ),
    Service(
        "cardiac_surgery", "Cardiac surgery",
        ("cardiac surgery", "heart surgery", "bypass", "cabg", "open heart", "valve replacement", "cardiothoracic"),
        strong=(r"\bcardiac surg", r"\bcardio ?thoracic", r"\bcabg\b", r"\bbypass surg", r"\bopen heart",
                r"\b(?:heart )?valve (?:replacement|repair|surg)", r"\bheart surg", r"\bcvts\b"),
        support=(r"\bcardiolog", r"\bcath(?:eteri[sz]ation)? lab", r"\bangioplast", r"\bccu\b", r"\bcardiac icu"),
        urgency="elective", tier="tertiary",
        nfhs=("blood pressure", "hypertension", "tobacco"),
        advice="Cardiac surgery is usually planned: ask for a cardiothoracic surgeon consultation and cost estimate first.",
    ),
    Service(
        "cath_lab", "Angioplasty / cath lab (heart attack care)",
        ("angioplasty", "cath lab", "heart attack", "stent", "angiography", "ptca", "pci"),
        strong=(r"\bcath(?:eteri[sz]ation)? lab", r"\bangioplast", r"\bcoronary angiogra", r"\bptca\b",
                r"\bprimary pci\b", r"\bpci\b", r"\bcoronary stent"),
        support=(r"\bcardiolog", r"\bccu\b", r"\bcardiac icu", r"\becho ?cardiogra", r"\b2d echo"),
        urgency="emergency", tier="tertiary",
        nfhs=("blood pressure", "hypertension", "tobacco"),
        advice="For a suspected heart attack, call ahead to confirm the cath lab is staffed 24x7.",
    ),
    Service(
        "mri", "MRI scan",
        ("mri", "magnetic resonance", "mri scan"),
        strong=(r"\bmri\b", r"\bmagnetic resonance", r"\bmr (?:imaging|angiography)"),
        support=(r"\bradiolog", r"\bdiagnostic imaging", r"\bimaging (?:centre|center|department)"),
        urgency="elective", tier="tertiary",
        advice="Ask about waiting time and whether a radiologist report is given the same day.",
    ),
    Service(
        "ct_scan", "CT scan",
        ("ct", "ct scan", "cat scan", "computed tomography"),
        strong=(r"\bct (?:scan|scanner|imaging|angio)", r"\bcomputed tomograph", r"\bcat scan", r"\b\d{2,3} slice"),
        support=(r"\bradiolog", r"\bdiagnostic imaging"),
        urgency="urgent", tier="secondary",
        advice="For head injury or stroke, ask whether CT is available 24x7.",
    ),
    Service(
        "burn_care", "Burn care",
        ("burn", "burns", "burn unit", "burn ward", "burn injury"),
        strong=(r"\bburns? (?:unit|ward|care|centre|center|icu|treatment|management|surgery|injur)",
                r"\bburn(?:s)? and plastic", r"\btreat(?:ment|s|ing)? (?:of )?burns?\b"),
        support=(r"\bplastic surg", r"\bskin graft", r"\breconstructive surg"),
        urgency="emergency", tier="tertiary",
        nfhs=("clean fuel", "cooking fuel"),
        advice="Serious burns need a dedicated burns unit: confirm one exists before travelling far.",
    ),
    Service(
        "trauma", "Trauma / accident emergency",
        ("trauma", "accident", "road accident", "polytrauma", "injury", "casualty"),
        strong=(r"\btrauma (?:centre|center|care|unit|surg|team|ward|icu)", r"\bpoly ?trauma",
                r"\baccident and emergency", r"\bcasualty\b", r"\blevel (?:i|1|ii|2) trauma", r"\bemergency trauma"),
        support=(r"\bemergency (?:department|services?|care|room|ward)", r"\borthop(?:a)?edic", r"\bneuro ?surg",
                 r"\bambulance", r"\bicu\b", r"\bct (?:scan|scanner)"),
        urgency="emergency", tier="secondary",
        advice="Call ahead so the trauma team, blood and CT are ready on arrival.",
    ),
    Service(
        "emergency", "24x7 emergency care",
        ("emergency", "er", "emergency room", "24x7", "24 hours", "urgent care"),
        strong=(r"\bemergency (?:department|room|services?|care|ward|unit|medicine)", r"\b24 ?x ?7 emergency",
                r"\b24 7 emergency", r"\bround the clock emergency", r"\bcasualty\b"),
        support=(r"\bambulance", r"\b24 ?x ?7\b", r"\b24 7\b", r"\b24 hours?\b"),
        urgency="emergency", tier="primary",
        advice="Call the emergency number on arrival; ask whether a doctor is on site now.",
    ),
    Service(
        "icu", "ICU / critical care",
        ("icu", "intensive care", "critical care", "ventilator", "micu", "sicu"),
        strong=(r"\bicu\b", r"\bintensive care", r"\bcritical care", r"\b[ms]icu\b", r"\bventilators?\b"),
        support=(r"\bhdu\b", r"\bhigh dependency"),
        urgency="emergency", tier="secondary",
        advice="ICU beds fill quickly: confirm a bed (and ventilator if needed) is free before transfer.",
    ),
    Service(
        "oncology", "Cancer treatment (chemotherapy / oncology)",
        ("cancer", "oncology", "chemotherapy", "chemo", "tumour", "tumor"),
        strong=(r"\bchemo ?therap", r"\b(?:medical |surgical |radiation )?oncolog", r"\bcancer (?:treatment|care|hospital|centre|center|surg)",
                r"\btumou?r board"),
        support=(r"\bbiops", r"\bmammogra", r"\bpap smear", r"\bpet ct"),
        urgency="elective", tier="tertiary",
        nfhs=("cancer", "screen", "tobacco"),
        advice="Ask whether the facility offers the specific treatment (chemo, surgery or radiation) and accepts government schemes.",
    ),
    Service(
        "radiotherapy", "Radiotherapy",
        ("radiotherapy", "radiation therapy", "linac", "brachytherapy"),
        strong=(r"\bradio ?therap", r"\bradiation (?:therapy|oncology)", r"\blinac\b", r"\blinear accelerator",
                r"\bbrachy ?therap", r"\btomotherapy", r"\bcobalt (?:60|unit|therapy)"),
        support=(r"\boncolog", r"\bcancer"),
        urgency="elective", tier="tertiary",
        nfhs=("cancer", "screen"),
        advice="Radiotherapy machines often have waiting lists: ask about the next available start date.",
    ),
    Service(
        "blood_bank", "Blood bank / transfusion",
        ("blood bank", "blood transfusion", "blood", "transfusion"),
        strong=(r"\bblood bank", r"\bblood (?:storage|centre|center|transfusion)", r"\btransfusion"),
        support=(r"\bpatholog",),
        urgency="emergency", tier="secondary",
        nfhs=("anaemi", "anemi"),
        advice="Ask whether the required blood group is in stock.",
    ),
    Service(
        "neurosurgery", "Neurosurgery (brain / spine)",
        ("neurosurgery", "brain surgery", "spine surgery", "head injury"),
        strong=(r"\bneuro ?surg", r"\bcraniotom", r"\bbrain (?:surgery|tumou?r surg)", r"\bspin(?:e|al) surg"),
        support=(r"\bneurolog", r"\bct (?:scan|scanner)", r"\bmri\b"),
        urgency="urgent", tier="tertiary",
        advice="For head injury, confirm a neurosurgeon is on call and CT is working.",
    ),
    Service(
        "maternity", "Childbirth / maternity care",
        ("delivery", "childbirth", "maternity", "pregnancy", "labour", "antenatal", "normal delivery"),
        strong=(r"\bmaternity\b", r"\blabou?r (?:room|ward)", r"\b(?:normal|institutional) deliver", r"\bobstetric",
                r"\bantenatal", r"\bdeliver(?:y|ies)\b"),
        support=(r"\bgyn(?:a)?ecolog", r"\bpostnatal", r"\bwomen s (?:health|hospital)"),
        urgency="urgent", tier="primary",
        nfhs=("institutional birth", "antenatal", "caesarean", "births attended"),
        advice="Register early for antenatal visits; ask whether the facility handles complications or refers out.",
    ),
    Service(
        "transplant", "Organ transplant (kidney, liver, heart, bone marrow)",
        ("transplant", "kidney transplant", "liver transplant", "organ transplant"),
        strong=(r"\b(?:kidney|renal|liver|heart|lung|pancreas|bone marrow|stem cell|organ) transplant",
                r"\b(?<!corneal )(?<!cornea )(?<!hair )transplant(?:ation)? (?:unit|centre|center|surg|program)"),
        support=(r"\bnephrolog", r"\bhepatolog"),
        urgency="elective", tier="tertiary",
        advice="Transplants require a licensed centre: ask for their transplant authorisation and waiting list.",
    ),
    Service(
        "cataract", "Eye care / cataract surgery",
        ("cataract", "eye surgery", "ophthalmology", "eye hospital", "eye care"),
        strong=(r"\bcataract", r"\bphaco", r"\bophthalm", r"\beye (?:surg|hospital|care|clinic)", r"\blasik\b", r"\bretina",
                r"\bcornea[l]? transplant", r"\bkeratoplast"),
        support=(r"\boptometr", r"\bvision\b"),
        urgency="elective", tier="secondary",
        advice="Ask whether surgery is done on-site and about camp/free schemes.",
    ),
    Service(
        "joint_replacement", "Orthopaedic / joint replacement",
        ("joint replacement", "knee replacement", "hip replacement", "orthopaedic", "orthopedic", "fracture"),
        strong=(r"\b(?:joint|knee|hip|shoulder) replacement", r"\barthroplast", r"\bfracture (?:surg|fixation|treatment)",
                r"\bortho(?:pa?edic)? surg"),
        support=(r"\borthop(?:a)?edic", r"\barthroscop", r"\bx ray\b"),
        urgency="elective", tier="secondary",
        advice="Ask about the surgeon's experience with the specific procedure and implant costs.",
    ),
    Service(
        "psychiatry", "Mental health / psychiatry",
        ("mental health", "psychiatry", "psychiatrist", "depression", "de-addiction", "deaddiction"),
        strong=(r"\bpsychiatr", r"\bmental health", r"\bde ?addiction", r"\bpsycholog"),
        support=(r"\bcounsel", r"\bneurolog"),
        urgency="elective", tier="secondary",
        nfhs=("alcohol", "tobacco"),
        advice="Ask whether in-patient care is available if needed.",
    ),
]

SERVICES_BY_ID = {s.id: s for s in SERVICES}


def match_service(query: str) -> Service | None:
    """Map a free-text service request (e.g. 'kidney dialysis', 'C-section') to a Service."""
    from .text import norm

    q = norm(query)
    if not q:
        return None
    if q in SERVICES_BY_ID:
        return SERVICES_BY_ID[q]
    best, best_len = None, 0
    for s in SERVICES:
        for a in (s.label,) + s.aliases:
            a_n = norm(a)
            if a_n and (a_n == q or re.search(rf"\b{re.escape(a_n)}\b", q)) and len(a_n) > best_len:
                best, best_len = s, len(a_n)
    if best:
        return best
    for s in SERVICES:  # fall back to the evidence patterns themselves
        if s.strong_re().search(q):
            return s
    return None


# Facility-level readiness signal for emergency services
EMERGENCY_24X7_RE = re.compile(
    r"\b24 ?x ?7\b|\b24 7\b|\b24 hours?\b|\bround the clock\b|\baround the clock\b|\bemergency (?:department|services?|room|care)\b|\bcasualty\b"
)

# Negation / not-actually-available cues around a match (checked in a short window)
NEGATION_BEFORE_RE = re.compile(
    r"\b(?:no|not|without|lacks?|lacking|absence of|refer(?:s|red)? (?:out|to|for)|planned|upcoming|proposed|"
    r"coming soon|under construction|will (?:soon )?(?:start|open|have|include|add|launch|introduce)|future|"
    r"plans? to (?:add|start|open|launch|introduce|set up|establish)|phase (?:ii|2|iii|3)|to be (?:added|started|set up))\b"
    r"(?:\W+\w+){0,4}\W*$"
)
NEGATION_AFTER_RE = re.compile(
    r"^\W*(?:\w+\W+){0,3}?(?:not available|unavailable|coming soon|planned|under construction|is not (?:offered|provided)|referred (?:out|to))"
)
