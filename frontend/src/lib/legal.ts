/**
 * Shared legal/company facts for the public policy pages.
 *
 * Centralised so the Terms, Privacy, Cookie, and DMCA pages cannot drift
 * apart on the details that matter most in a dispute (legal entity, address,
 * designated contact, effective date). Operator note: every field here —
 * especially the entity name, registered address, governing law, and the
 * contact addresses — must match the company's actual registration and
 * monitored mailboxes before launch.
 */

export const COMPANY = {
  legalName: "ContractOS (Pvt) Ltd",
  productName: "ContractOS",
  addressLines: ["121 Galle Road", "Colombo 03", "Sri Lanka"],
  governingLaw: "the laws of Sri Lanka",
  effectiveDate: "October 2, 2026",
} as const;

export const LEGAL_CONTACTS = {
  general: "legal@contractos.lk",
  privacy: "privacy@contractos.lk",
  dpo: "dpo@contractos.lk",
  security: "security@contractos.lk",
  dmca: "dmca@contractos.lk",
} as const;

export const MINIMUM_AGE = 13;
