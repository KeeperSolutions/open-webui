export type PricingPlan = {
	name: string;
	tagline: string;
	currency: '€' | '$' | '£';
	price: number;
	priceSuffix?: string;
	seatPrice?: number;
	creditsHighlight?: string;
	creditsLabel?: string;
	features: string[];
	ctaLabel: string;
	isMostPopular?: boolean;
	note?: string;
	postLoginRedirect?: string;
};

export const plans: PricingPlan[] = [
	{
		name: 'Free Trial',
		tagline: 'Test the water — every model, fully governed',
		currency: '€',
		price: 0,
		creditsHighlight: '€ 2.00',
		creditsLabel: 'exploratory credit',
		features: ['Claude, Gemini, GPT-4o & Perplexity', 'Automatic PII masking', '10-second sign-up'],
		ctaLabel: 'Start Free Trial'
	},
	{
		name: 'Pro',
		tagline: 'For occasional AI assistance',
		currency: '€',
		price: 15,
		priceSuffix: '/ per month',
		creditsHighlight: '1,300',
		creditsLabel: 'credits / month',
		features: ['All models, one interface', 'Automatic PII masking', 'Pay-as-you-go top-ups'],
		ctaLabel: 'Get Pro',
		postLoginRedirect: '/billing'
	},
	{
		name: 'Premium',
		tagline: 'For heavy daily AI workflows',
		currency: '€',
		price: 45,
		priceSuffix: '/ per month',
		seatPrice: 15,
		creditsHighlight: '3,800',
		creditsLabel: 'credits / month',
		features: [
			'Auto top-up & spend hard caps',
			'Custom system prompts',
			'Personal knowledge bases'
		],
		ctaLabel: 'Get Premium',
		isMostPopular: true,
		postLoginRedirect: '/billing'
	},
	{
		name: 'Business',
		tagline: 'Govern and scale AI across your organisation',
		currency: '€',
		price: 79,
		priceSuffix: '/ workspace',
		seatPrice: 15,
		creditsHighlight: '1,000',
		creditsLabel: 'pooled credits / seat',
		features: [
			'Full audit trail & admin centre',
			'Single Sign-On (Google / Microsoft)',
			'Shared knowledge bases & agents',
			'Know your AI usage report'
		],
		ctaLabel: 'Create a workspace',
		postLoginRedirect: '/billing'
	}
];
