export type PricingPlan = {
	name: string;
	tagline: string;
	currency: '€' | '$' | '£';
	price: number;
	priceSuffix?: string;
	features: string[];
	ctaLabel: string;
	isMostPopular?: boolean;
	note?: string;
	postLoginRedirect?: string;
};

export const plans: PricingPlan[] = [
	{
		name: 'Free Trial',
		tagline: 'Test the water',
		currency: '€',
		price: 0,
		features: [
			'€ 2.00 exploratory credit included',
			'Claude, Gemini, GPT-4o & Perplexity',
			'Automatic PII masking',
			'No credit card required'
		],
		ctaLabel: 'Start Free Trial'
	},

	{
		name: 'Premium',
		tagline: 'For heavy daily AI workflows',
		currency: '€',
		price: 45,
		priceSuffix: '/ per month',
		features: [
			'Optional auto top-up',
			'Configurable spend hard caps',
			'Automatic PII masking',
			'Custom system prompts',
			'Knowledge bases'
		],
		ctaLabel: 'Get Premium',
		isMostPopular: true,
		postLoginRedirect: '/billing'
	},
	{
		name: 'Team',
		tagline: 'Govern and scale AI across your organisation',
		currency: '€',
		price: 45,
		priceSuffix: '/ seat/month',
		features: [
			'Centralised team billing',
			'Team leaderboard & usage analytics',
			'Single Sign-On (Google / Microsoft)',
			'Shared company knowledge bases',
			'Dedicated admin control centre',
			'Everything in Premium Plan'
		],
		ctaLabel: 'Create a Team',
		note: 'Volume discounts available — contact us',
		postLoginRedirect: '/billing'
	}
];
