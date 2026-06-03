import type { Component } from 'svelte';
import HgIllustrationPrivacy from '$lib/components/hubgate/illustrations/HgIllustrationPrivacy.svelte';
import HgIllustrationMasking from '$lib/components/hubgate/illustrations/HgIllustrationMasking.svelte';
import HgIllustrationKnowledge from '$lib/components/hubgate/illustrations/HgIllustrationKnowledge.svelte';
import HgIllustrationAgents from '$lib/components/hubgate/illustrations/HgIllustrationAgents.svelte';
import HgIllustrationControl from '$lib/components/hubgate/illustrations/HgIllustrationControl.svelte';
import HgIllustrationModels from '$lib/components/hubgate/illustrations/HgIllustrationModels.svelte';

export type Feature = {
	title: string;
	description: string;
	illustration: Component;
};

export const features: Feature[] = [
	{
		title: 'Keep Your Business Data Private',
		description: 'Your prompts and responses never train AI models.',
		illustration: HgIllustrationPrivacy
	},
	{
		title: 'Automatically Mask Sensitive Information',
		description: 'Names, IBANs, emails and more are hidden before they reach any AI.',
		illustration: HgIllustrationMasking
	},
	{
		title: 'Get Accurate Answers From Your Own Files',
		description:
			'Feed your documents into any AI you choose. Get fast, grounded answers based on your actual data.',
		illustration: HgIllustrationKnowledge
	},
	{
		title: 'Automate Workflows With Specialized Assistants',
		description:
			"Deploy agents like QA Reviewer or Sales Drafter tailored to your team's needs — ready in minutes.",
		illustration: HgIllustrationAgents
	},
	{
		title: 'Maintain Full Control and Compliance',
		description:
			'Every prompt logged, every session tracked. Built for security reviews and compliance audits.',
		illustration: HgIllustrationControl
	},
	{
		title: 'Access Leading AI Models in One Place',
		description:
			'Claude, Gemini, GPT-4o, Perplexity — switch between them seamlessly without switching tools.',
		illustration: HgIllustrationModels
	}
];
