export type Plan = {
	name: string;
	subtitle: string;
	price: string;
	period: string;
	badge: string | null;
	features: string[];
	cta: string;
	ctaHref: string;
	ctaStyle: 'primary' | 'secondary';
	footerNote?: string;
};

export const plans: Plan[] = [
	{
		name: 'Free Trial',
		subtitle: 'Test the water',
		price: '0',
		period: '/ per month',
		badge: null,
		features: [
			'€ 2.00 exploratory credit included',
			'Claude, Gemini, GPT-4o & Perplexity',
			'Automatic PII masking',
			'No credit card required'
		],
		cta: 'Start Free Trial',
		ctaHref: '/auth',
		ctaStyle: 'secondary'
	},
	{
		name: 'Pro',
		subtitle: 'For occasional AI assistance',
		price: '15',
		period: '/ per month',
		badge: 'Most Popular',
		features: [
			'Manual top-ups in €10 increments',
			'Export outputs',
			'Automatic PII masking',
			'Claude, Gemini, GPT-4o & Perplexity'
		],
		cta: 'Get Pro',
		ctaHref: '/auth',
		ctaStyle: 'primary'
	},
	{
		name: 'Premium',
		subtitle: 'For heavy daily AI workflows',
		price: '45',
		period: '/ per month',
		badge: null,
		features: [
			'Optional auto top-up',
			'Configurable spend hard caps',
			'Automatic PII masking',
			'Custom system prompts',
			'Knowledge bases'
		],
		cta: 'Get Premium',
		ctaHref: '/auth',
		ctaStyle: 'secondary'
	},
	{
		name: 'Team',
		subtitle: 'Govern and scale AI across your organisation',
		price: '45',
		period: '/ seat/month',
		badge: null,
		features: [
			'Centralised team billing',
			'Team leaderboard & usage analytics',
			'Single Sign-On (Google / Microsoft)',
			'Shared company knowledge bases',
			'Dedicated admin control centre',
			'Everything in Premium Plan'
		],
		cta: 'Create a Team',
		ctaHref: '/auth',
		ctaStyle: 'secondary',
		footerNote: 'Volume discounts available — contact us'
	}
];
