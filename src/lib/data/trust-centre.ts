import type { Component } from 'svelte';

// Reused existing icons
import HgIconLock from '$lib/components/icons/HgIconLock.svelte';
import HgIconShieldPixel from '$lib/components/icons/HgIconShieldPixel.svelte';
import HgIconDocument from '$lib/components/icons/HgIconDocument.svelte';
import HgIconPolicy from '$lib/components/icons/HgIconPolicy.svelte';
import HgIconPixelMask from '$lib/components/icons/HgIconPixelMask.svelte';

// Icons not yet created — Task 3 creates these exact components.
import HgIconCertificate from '$lib/components/icons/HgIconCertificate.svelte';
import HgIconGlobe from '$lib/components/icons/HgIconGlobe.svelte';
import HgIconRoute from '$lib/components/icons/HgIconRoute.svelte';
import HgIconBan from '$lib/components/icons/HgIconBan.svelte';
import HgIconFingerprint from '$lib/components/icons/HgIconFingerprint.svelte';
import HgIconList from '$lib/components/icons/HgIconList.svelte';
import HgIconUsers from '$lib/components/icons/HgIconUsers.svelte';
import HgIconRefresh from '$lib/components/icons/HgIconRefresh.svelte';
import HgIconLayers from '$lib/components/icons/HgIconLayers.svelte';
import HgIconRobot from '$lib/components/icons/HgIconRobot.svelte';
import HgIconShieldLock from '$lib/components/icons/HgIconShieldLock.svelte';
import HgIconSearchBug from '$lib/components/icons/HgIconSearchBug.svelte';
import HgIconAlertCaution from '$lib/components/icons/HgIconAlertCaution.svelte';
import HgIconFileNote from '$lib/components/icons/HgIconFileNote.svelte';
import HgIconCheckCircle from '$lib/components/icons/HgIconCheckCircle.svelte';
import HgIconGlobePublic from '$lib/components/icons/HgIconGlobePublic.svelte';
import HgIconScale from '$lib/components/icons/HgIconScale.svelte';

export type BadgeVariant = 'success' | 'info' | 'warning' | 'neutral' | 'orange';

export interface TrustPill {
	icon: Component;
	label: string;
}
export interface TrustPillar {
	icon: Component;
	title: string;
	body: string;
	color?: 'green' | 'blue' | 'orange';
}
export interface TrustCert {
	name: string;
	badge: { label: string; variant: BadgeVariant };
	body: string;
	// Trailing placeholder segment rendered in red (matches Figma)
	bodyAccent?: string;
}
export interface TrustListItem {
	icon: Component;
	title: string;
	body: string;
}
export interface SpecRow {
	label: string;
	value?: string;
	badge?: { label: string; variant: BadgeVariant };
	flag?: 'fr' | 'eu' | 'us';
}
export interface TrustDownload {
	icon: Component;
	title: string;
	meta: string;
	href: string;
}
export interface TrustContact {
	label: string;
	email: string;
}

export interface SectionCopy {
	eyebrow: string;
	title: string;
	description: string;
}

// ── Hero ──────────────────────────────────────────────────────────────────────
export const hero = {
	pill: 'No data used to train AI models',
	// h1 is rendered with "request path." in hg-orange (see +page.svelte)
	headingLead: 'Trust, built into the ',
	headingAccent: 'request path.',
	description: 'Hubgate is where security, privacy and compliance are demonstrated — not promised.'
};

// ── Signature: PII masking transform (static) ──────────────────────────────────
export const piiTransform = {
	toggleLabel: 'PII Masking',
	toggleState: 'On',
	inputHeading: 'What you type',
	inputContext: '· inside your perimeter',
	outputHeading: 'What the model receives',
	// Segments render inline; { pii: true } segments are underlined (input) / tokenised (output)
	inputSegments: [
		{ text: 'Please summarise the contract for ' },
		{ text: 'Aoife Brennan', pii: true },
		{ text: ', who called from ' },
		{ text: '+353 87 219 4471', pii: true },
		{ text: ' about account ' },
		{ text: 'IE29 8842 1077', pii: true },
		{ text: '.' }
	],
	outputSegments: [
		{ text: 'Please summarise the contract for ' },
		{ token: '[PERSON_1]', variant: 'blue'},
		{ text: ', who called from ' },
		{ token: '[PHONE_1]', variant: 'green' },
		{ text: ' about account ' },
		{ token: '[ACCOUNT_1]', variant: 'amber' },
		{ text: '.' }
	],
	footnote: 'Real values are re-inserted in the response — the model and its logs never see them.',
	// "emphasis" renders in text-primary; the rest in text-tertiary (matches Figma)
	caption: {
		before: 'Evidential control, not a pinky promise. ',
		emphasis: 'Toggle masking',
		after: ' to see what leaves your perimeter.'
	}
};

// Icons: import the referenced components once at top of file, e.g.
//   import HgIconLock from '$lib/components/icons/HgIconLock.svelte';
// Task 3 guarantees each referenced icon component exists.

// ── Marquee pills (block 4) ────────────────────────────────────────────────────
export const marqueePills: TrustPill[] = [
	{ icon: HgIconShieldPixel, label: 'ISO 27001 certified' },
	{ icon: HgIconCheckCircle, label: 'GDPR compliant' },
	{ icon: HgIconGlobePublic, label: 'EU hosted & EU models' },
	{ icon: HgIconScale, label: 'EU AI Act aligned' },
	{ icon: HgIconLock, label: 'DORA ready' }
];

// ── Pillars: "Six commitments…" (block 5) ────────────────────────────────────
export const pillarsSection: SectionCopy = {
	eyebrow: 'the promise',
	title: 'Six commitments your security team can verify.',
	description:
		'Every claim below maps to a control you can inspect, test or audit — designed for procurement reviews in regulated sectors.'
};
export const pillars: TrustPillar[] = [
	{
		icon: HgIconShieldPixel,
		color: 'green',
		title: 'Your data stays yours',
		body: 'Prompts and outputs are never used to train models. Providers are contractually barred from training on your content.'
	},
	{
		icon: HgIconFingerprint,
		color: 'blue',
		title: 'PII masked before it leaves',
		body: 'A dual-stage filter detects and replaces personal data in the request path — before any prompt reaches an external model.'
	},
	{
		icon: HgIconGlobe,
		color: 'orange',
		title: 'EU hosting & EU models',
		body: 'Data is hosted and processed in the EU, with European models — including Mistral (France) — available in the catalogue.'
	},
	{
		icon: HgIconLock,
		color: 'blue',
		title: 'Encryption everywhere',
		body: 'TLS 1.3 in transit and AES-256 at rest, with managed keys. Sensitive data is protected end to end across the platform.'
	},
	{
		icon: HgIconList,
		color: 'blue',
		title: 'Immutable audit trail',
		body: 'Every prompt, response and session is logged and tamper-evident — ready to export for a security review or compliance audit.'
	},
	{
		icon: HgIconUsers,
		color: 'orange',
		title: 'Access you govern',
		body: 'SSO, role-based access and admin-defined policies put control of who can do what — and with which models — in your hands.'
	}
];

// ── Hosting & residency (block 6) ────────────────────────────────────────────
export const hostingSection: SectionCopy = {
	eyebrow: 'Hosting & data residency',
	title: 'Sovereign by default.\nEuropean by design.',
	description:
		'Where your data lives and which models touch it are explicit choices — not buried in a sub-processor list.'
};
export const hostingList: TrustListItem[] = [
	{
		icon: HgIconGlobe,
		title: 'EU data residency',
		body: 'Workspace data is stored and processed in EU regions — hosted with [?]' // TODO host name
	},
	{
		icon: HgIconLayers,
		title: 'European models in the catalogue',
		body: 'Route to EU-built models such as Mistral (France) when sovereignty matters, alongside leading global models.'
	},
	{
		icon: HgIconRoute,
		title: 'Per-policy model routing',
		body: 'Admins choose which models are available to which teams — and can restrict workloads to EU-only inference.'
	},
	{
		icon: HgIconBan,
		title: 'No training, contractually',
		body: 'All providers in the catalogue operate under no-training, no-retention terms for your content.'
	}
];
export const modelCatalogue: SpecRow[] = [
	// Header badge (rendered separately by the table component)
	{ label: 'Model catalogue', badge: { label: 'No training on your data', variant: 'success' } },
	{ label: 'Mistral Large', flag: 'fr', badge: { label: 'EU inference', variant: 'info' } },
	{ label: 'Mistral / EU open models', flag: 'eu', badge: { label: 'EU inference', variant: 'info' } },
	{ label: 'Claude (Anthropic)', flag: 'us', badge: { label: 'EU endpoint', variant: 'neutral' } },
	{ label: 'GPT (OpenAI)', flag: 'us', badge: { label: 'EU endpoint', variant: 'neutral' } },
	{ label: 'Perplexity · real-time search', flag: 'us', badge: { label: 'Search', variant: 'neutral' } }
];

// ── Encryption band (block 7) ────────────────────────────────────────────────
export const encryptionSection: SectionCopy = {
	eyebrow: 'Encryption & data protection',
	title: 'Protected end to end, and\nde-identified by default.',
	description:
		'Strong cryptography is the floor. The differentiator is that personal data is removed from the prompt itself — so even an exposed log holds nothing sensitive.'
};
export const dataHandlingRows: SpecRow[] = [
	{ label: 'In transit', badge: { label: 'TLS 1.3', variant: 'info' } },
	{ label: 'At rest', badge: { label: 'AES-256', variant: 'info' } },
	{ label: 'Key management', badge: { label: 'Managed KMS', variant: 'neutral' } },
	{ label: 'PII in logs', badge: { label: 'Masked', variant: 'info' } },
	{ label: 'Retention', badge: { label: '[configurable]', variant: 'success' } }
];
export const encryptionList: TrustListItem[] = [
	{
		icon: HgIconFingerprint,
		title: 'Dual-stage PII detection',
		body: 'Built on proven open-source NER (Presidio, spaCy) with proprietary tuning for finance, legal and healthcare entities.'
	},
	{
		icon: HgIconRefresh,
		title: 'Reversible only inside your perimeter',
		body: "The token-to-value map stays within the gateway; real values are restored to responses, never sent onward."
	},
	{
		icon: HgIconPolicy,
		title: 'Policy-controlled, not optional',
		body: "Admins can enforce masking for whole teams or document types — users can't silently turn protection off."
	}
];

// ── Certifications (block 8) ─────────────────────────────────────────────────
export const certsSection: SectionCopy = {
	eyebrow: 'Certifications & regulatory posture',
	title: "Honest about what's certified —\nand what we support.",
	description:
		'We separate certifications we hold from regulations we help you meet. That distinction is exactly what a procurement team is looking for.'
};
export const certifications: TrustCert[] = [
	{
		name: 'ISO 27001',
		badge: { label: 'Certified', variant: 'success' },
		body: 'Keeper Solutions operates a certified information security management system. Certificate ',
		bodyAccent: 'no. []' // TODO cert no.
	},
	{
		name: 'GDPR',
		badge: { label: 'Compliant', variant: 'success' },
		body: 'Lawful processing, data-subject rights and a Data Processing Agreement available on request. EU data residency by default.'
	},
	{
		name: 'EU AI Act',
		badge: { label: 'Aligned', variant: 'info' },
		body: 'Designed around transparency, logging and human-oversight obligations to support your AI Act readiness as requirements phase in.'
	},
	{
		name: 'DORA',
		badge: { label: 'Supports', variant: 'orange' },
		body: 'Immutable logging, audit exports and provider transparency support the ICT risk and third-party obligations under DORA.'
	},
	{
		name: 'SOC 2',
		badge: { label: 'In progress', variant: 'orange' },
		body: 'Controls aligned to the Trust Services Criteria',
		bodyAccent: '. [update status / timeline]' // TODO status/timeline
	},
	{
		name: 'Pen testing',
		badge: { label: 'Annual', variant: 'info' },
		body: 'Independent penetration testing with summary reports for customers.',
		bodyAccent: ' Last test: [date]' // TODO date
	}
];

// ── Audit signature (block 9) ────────────────────────────────────────────────
export const auditSection: SectionCopy = {
	eyebrow: 'Audit & monitoring',
	title: 'Every prompt logged.\nEvery session tracked.',
	description:
		'Built for security reviews and compliance audits. Export a complete, tamper-evident record whenever a regulator or client asks — with PII already masked.'
};
export const auditList: TrustListItem[] = [
	{
		icon: HgIconDocument,
		title: 'Immutable, exportable logs',
		body: 'Model, provider, token usage and masked-prompt counts per call.'
	},
	{
		icon: HgIconPixelMask,
		title: 'Pseudonymised by design',
		body: 'User identifiers can be pseudonymised before any external sharing.'
	}
];
export const auditStats = [
	{ value: '247', label: 'Active users' },
	{ value: '18', label: 'Active policies' },
	{ value: '100%', label: 'Sessions logged' }
];

// ── Know Your Agent band (block 10) ──────────────────────────────────────────
export const kyaSection: SectionCopy = {
	eyebrow: 'Responsible AI & security operations',
	title: 'Know Your Agent — and\nthe practices behind it.',
	description:
		'Governance that bridges policy and engineering: the same evidential discipline applied to how we build and run the platform.'
};
export const kyaCards: TrustPillar[] = [
	{
		icon: HgIconRobot,
		color: 'blue',
		title: 'Know Your Agent (KYA)',
		body: 'Every agent is documented — purpose, data access, models and limits.'
	},
	{
		icon: HgIconShieldLock,
		color: 'orange',
		title: 'Secure SDLC',
		body: 'Security and PII controls are built in from day one, not added after the fact.'
	},
	{
		icon: HgIconSearchBug,
		color: 'green',
		title: 'Vulnerability management',
		body: 'Continuous scanning, regular patching and annual pen testing with shareable reports.'
	},
	{
		icon: HgIconAlertCaution,
		color: 'blue',
		title: 'Incident response',
		body: 'A defined breach-notification process aligned to GDPR and DORA timelines.'
	}
];

// ── Downloads (block 11) ─────────────────────────────────────────────────────
export const downloadsSection: SectionCopy = {
	eyebrow: 'Document library',
	title: 'The evidence, ready to download.',
	description: 'Everything your security and procurement teams typically request — in one place.'
};
export const downloads: TrustDownload[] = [
	{
		icon: HgIconFileNote,
		title: 'Security whitepaper',
		meta: 'Architecture, PII masking & controls · PDF',
		href: '/hubgate/documents/security-whitepaper.pdf'
	},
	{
		icon: HgIconCertificate,
		title: 'ISO 27001 certificate',
		meta: 'Keeper Solutions ISMS · PDF',
		href: '/hubgate/documents/iso-27001-certificate.pdf'
	}
];

// ── Contact CTA (block 12) ───────────────────────────────────────────────────
export const contactCta = {
	title: 'Talk to our security team.',
	body: "Need a completed questionnaire, a signed DPA, or a walkthrough of the masking architecture? We'll get you what your review needs."
};
export const contacts: TrustContact[] = [
	{ label: 'Security & trust enquiries', email: 'security@hubgate.io' },
	{ label: 'Report a vulnerability', email: 'disclosure@hubgate.io' }
];
