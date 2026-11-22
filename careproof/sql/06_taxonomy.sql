-- One row per normalized service alias. All aliases are retrieval cues.
-- For context matching, use one clause or list item from one source field.
-- Keep the original span for evidence, and normalize a copy with the same
-- lowercasing, punctuation-to-space replacement and trim used for aliases.
-- Apply in order: alias absent -> no_match; negation -> negated;
-- contextual mention or missing offering cue -> mention_only;
-- otherwise -> candidate_claim, pending facility attribution and review.
-- Treat conflicting clauses separately. A candidate claim is a dataset
-- statement and carries no assertion of current availability.
CREATE OR REPLACE TABLE workspace.careproof.service_taxonomy
USING DELTA
AS
WITH aliases(service_key, service_name, raw_alias) AS (
  VALUES
    ('dialysis', 'Dialysis', 'dialysis'),
    ('dialysis', 'Dialysis', 'hemodialysis'),
    ('dialysis', 'Dialysis', 'haemodialysis'),
    ('dialysis', 'Dialysis', 'peritoneal dialysis'),
    ('dialysis', 'Dialysis', 'renal dialysis'),
    ('mri', 'MRI', 'MRI'),
    ('mri', 'MRI', 'magnetic resonance imaging'),
    ('nicu', 'Neonatal intensive care', 'NICU'),
    ('nicu', 'Neonatal intensive care', 'neonatal ICU'),
    ('nicu', 'Neonatal intensive care', 'neonatal intensive care'),
    ('nicu', 'Neonatal intensive care', 'newborn intensive care'),
    ('emergency_c_section', 'Emergency C-section', 'emergency C-section'),
    ('emergency_c_section', 'Emergency C-section', 'emergency cesarean'),
    ('emergency_c_section', 'Emergency C-section', 'emergency caesarean'),
    ('emergency_c_section', 'Emergency C-section', 'emergency cesarean delivery'),
    ('emergency_c_section', 'Emergency C-section', 'emergency caesarean section'),
    ('cardiac_surgery', 'Cardiac surgery', 'cardiac surgery'),
    ('cardiac_surgery', 'Cardiac surgery', 'heart surgery'),
    ('cardiac_surgery', 'Cardiac surgery', 'open-heart surgery'),
    ('cardiac_surgery', 'Cardiac surgery', 'coronary artery bypass grafting'),
    ('trauma_emergency_care', 'Trauma and emergency care', 'trauma care'),
    ('trauma_emergency_care', 'Trauma and emergency care', 'trauma center'),
    ('trauma_emergency_care', 'Trauma and emergency care', 'trauma centre'),
    ('trauma_emergency_care', 'Trauma and emergency care', 'emergency care'),
    ('trauma_emergency_care', 'Trauma and emergency care', 'emergency department'),
    ('trauma_emergency_care', 'Trauma and emergency care', 'accident and emergency')
),
normalized AS (
  SELECT DISTINCT
    service_key,
    service_name,
    trim(regexp_replace(lower(raw_alias), '[^a-z0-9]+', ' ')) AS alias
  FROM aliases
)
SELECT
  service_key,
  service_name,
  alias,
  concat('(^| )', alias, '( |$)') AS alias_regex,
  concat(
    '(^| )((no|without|does not (currently )?(offer|provide|perform)|cannot (offer|provide|perform)|no longer (offers|provides|performs)) (onsite |on site )?', alias,
    '|', alias, '( (services?|unit|scans?))? (is |are )?(unavailable|not available|not offered|not provided|not performed|discontinued))( |$)'
  ) AS negation_regex,
  concat(
    '(^| )((referred for|referral for|referrals for|referred elsewhere for|planned|proposed|considering|training in|awareness of) ', alias,
    '|', alias, '( (services?|unit|scans?))? (referrals?|training|education|awareness|research|consultations?|is planned|is proposed|coming soon))( |$)'
  ) AS mention_only_regex,
  concat(
    '(^| )(offers|provides|performs|we (offer|provide|perform)) (onsite |on site )?', alias, '( |$)'
  ) AS offering_regex
FROM normalized;
