<script lang="ts">
	import HgLandingHeader from '$lib/components/hubgate/HgLandingHeader.svelte';
	import HgLandingFooter from '$lib/components/hubgate/HgLandingFooter.svelte';
</script>

<svelte:head>
	<title>Terms of Use — Hubgate</title>
</svelte:head>

<div class="min-h-screen flex flex-col font-hg-body bg-hg-bg-surface">
	<HgLandingHeader />

	<main class="flex-1 max-w-3xl mx-auto w-full px-6 py-16">
		<div class="mb-10">
			<h1 class="font-hg-heading font-bold text-3xl text-hg-text-primary mb-2">
				HubGate.io Terms of Use
			</h1>
			<p class="text-sm text-hg-text-tertiary">Last updated: 26 May 2026</p>
		</div>

		<p class="text-hg-text-secondary leading-relaxed mb-10">
			HubGate.io is a managed AI gateway operated by Keeper Solutions that provides unified access
			to multiple large language models through a single interface. These Terms of Use outline the
			legal agreement for users, detailing eligibility requirements, account registration via OAuth
			or email, and the subscription-based billing structure managed through Stripe.
		</p>

		<hr class="border-hg-border mb-10" />

		<!-- Section 1 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				1. Acceptance of Terms
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					By accessing or using HubGate.io (the <strong class="text-hg-text-primary">"Platform"</strong>),
					you agree to be bound by these Terms of Use (the <strong class="text-hg-text-primary">"Terms"</strong>).
					If you are using the Platform on behalf of an organisation, you represent that you have the authority
					to bind that organisation to these Terms, and references to <strong class="text-hg-text-primary">"you"</strong>
					include that organisation.
				</p>
				<p>If you do not agree to these Terms, please do not use the Platform.</p>
				<p>
					HubGate.io is operated by <strong class="text-hg-text-primary">Keeper Solutions</strong>
					(referred to in these Terms as <strong class="text-hg-text-primary">"we"</strong>,
					<strong class="text-hg-text-primary">"us"</strong>, or
					<strong class="text-hg-text-primary">"our"</strong>).
				</p>
			</div>
		</section>

		<!-- Section 2 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				2. About the Platform
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					HubGate.io provides a managed AI gateway that gives individuals and organisations fast,
					unified access to multiple large language model (<strong class="text-hg-text-primary">"LLM"</strong>)
					providers — including but not limited to OpenAI, Anthropic, and Google — through a single interface.
				</p>
				<p>The Platform is designed for:</p>
				<ul class="list-disc pl-6 space-y-2">
					<li>
						<strong class="text-hg-text-primary">SMEs and technology startups</strong> seeking
						cost-effective, multi-model AI access without vendor lock-in.
					</li>
					<li>
						<strong class="text-hg-text-primary">Managers and team leads</strong> who need a simple
						way to provision AI access for their team alongside basic usage visibility and spend management.
					</li>
					<li>
						<strong class="text-hg-text-primary">Individuals</strong> who want to explore and use
						multiple AI models without maintaining separate accounts with each provider.
					</li>
				</ul>
				<p>
					HubGate.io is intended to provide a <strong class="text-hg-text-primary">governed, accessible AI access layer</strong>
					appropriate for SMEs and growing technology companies. It is not designed as a heavy-weight
					enterprise compliance or regulatory solution, and should not be relied upon as such without
					independent assessment of your organisation's specific requirements.
				</p>
			</div>
		</section>

		<!-- Section 3 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">3. Eligibility</h2>
			<div class="text-hg-text-secondary leading-relaxed">
				<p class="mb-3">To use the Platform you must:</p>
				<ul class="list-disc pl-6 space-y-2">
					<li>Be at least <strong class="text-hg-text-primary">18 years of age</strong>.</li>
					<li>Have the legal capacity to enter into a binding agreement in your jurisdiction.</li>
					<li>
						Not be located in, or acting on behalf of any entity in, a jurisdiction where access to
						the Platform is prohibited by applicable law or regulation.
					</li>
				</ul>
			</div>
		</section>

		<!-- Section 4 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				4. Account Registration
			</h2>
			<div class="space-y-6 text-hg-text-secondary leading-relaxed">
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">4.1 Sign-Up Methods</h3>
					<p class="mb-2">You may register for an account using:</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>Google OAuth (SSO)</li>
						<li>Microsoft OAuth (SSO)</li>
						<li>Email address and password</li>
					</ul>
					<p class="mt-2">
						You are responsible for maintaining the confidentiality of your credentials and for all
						activity that occurs under your account.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">4.2 Accuracy of Information</h3>
					<p>
						You agree to provide accurate, current, and complete information during registration and
						to keep that information up to date. We reserve the right to suspend or terminate
						accounts where information is found to be false or misleading.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">4.3 Free Trial Credit</h3>
					<p class="mb-2">
						Upon first registration, eligible new users receive a one-time <strong
							class="text-hg-text-primary">free credit allowance</strong
						> (currently <strong class="text-hg-text-primary">€2.00 equivalent</strong>) to explore
						the Platform. This credit:
					</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>Is non-transferable and has no cash value.</li>
						<li>May only be used for LLM queries through the Platform.</li>
						<li>Will not be renewed or replaced once exhausted.</li>
						<li>May be withdrawn or adjusted at our discretion with reasonable notice.</li>
					</ul>
					<p class="mt-2">
						Once your free credit is exhausted, continued access requires a paid subscription.
					</p>
				</div>
			</div>
		</section>

		<!-- Section 5 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				5. Subscriptions & Billing
			</h2>
			<div class="space-y-6 text-hg-text-secondary leading-relaxed">
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">5.1 Paid Plans</h3>
					<p>
						HubGate.io offers subscription plans billed on a monthly basis. Current plans and
						pricing are displayed on the Platform's pricing page. Prices are subject to change with
						reasonable advance notice.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">5.2 Team Subscriptions</h3>
					<p class="mb-2">
						A <strong class="text-hg-text-primary">billing administrator</strong> (e.g. a manager or
						team lead) may subscribe on behalf of a team and add multiple users under a single subscription.
						The billing administrator is responsible for:
					</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>All charges associated with the team subscription.</li>
						<li>Managing user access, including adding and removing team members.</li>
						<li>Ensuring team members comply with these Terms.</li>
					</ul>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">5.3 Payment Processing</h3>
					<p>
						All payments are processed securely by <strong class="text-hg-text-primary">Stripe</strong>.
						Keeper Solutions does not store your payment card details. By subscribing, you agree to
						Stripe's terms of service, available at
						<a
							href="https://stripe.com/legal"
							target="_blank"
							rel="noopener noreferrer"
							class="text-hg-blue hover:underline">stripe.com/legal</a
						>.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">5.4 Credit Allowances & Usage</h3>
					<p class="mb-2">
						Each subscription plan includes a <strong class="text-hg-text-primary">monthly credit allowance</strong>
						denominated in USD/EUR. This credit is consumed as you make LLM queries. Different models
						carry different costs, which are reflected in real-time credit deductions visible in your
						usage dashboard.
					</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>Credits <strong class="text-hg-text-primary">do not roll over</strong> between billing periods unless otherwise stated.</li>
						<li>When your credit allowance is exhausted mid-cycle, access to LLM queries will be <strong class="text-hg-text-primary">paused</strong> or, depending on your plan, additional credit may be charged automatically in agreed increments.</li>
						<li>You will receive notifications as you approach and reach your credit limit.</li>
					</ul>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">5.5 Overage Charges</h3>
					<ul class="list-disc pl-6 space-y-1">
						<li>When your monthly credit is exhausted, you will be prompted to purchase additional credit in increments. Access is paused if you decline.</li>
						<li>Additional credit increments may be charged automatically (pre-authorised) when your balance reaches zero. You may configure a hard cap via your account settings or team admin dashboard.</li>
					</ul>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">5.6 Custom Models</h3>
					<p class="mb-2">
						You may create your own custom models or add data sources (e.g. Google Drive) to the
						Platform. Keeper Solutions is not responsible for the content, accuracy, or behaviour of
						any custom model you configure. Usage of custom models may still consume credits
						depending on the underlying provider.
					</p>
					<p>
						By connecting third-party services or data sources (e.g. Google Drive) to the Platform,
						you grant us permission to access those services and send your input to them. You may
						only connect a third-party service if you have all necessary rights, licenses, and
						permissions to do so. HubGate is not responsible for any third-party service, including
						any disruptions, data loss, or issues with the Platform arising from your use of the
						third-party service.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">5.7 Invoices & Billing Portal</h3>
					<p>
						You may access your invoice history, update payment methods, and manage or cancel your
						subscription at any time through the <strong class="text-hg-text-primary">Stripe Customer Portal</strong>,
						accessible from your account settings.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">5.8 Cancellation</h3>
					<p>
						You may cancel your subscription at any time. Cancellation takes effect at the end of
						the current billing period. No partial refunds are provided for unused credit within a
						billing period, except where required by applicable law.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">5.9 Refunds & Right of Withdrawal</h3>
					<p>
						Except where required by law, subscription fees are <strong class="text-hg-text-primary">non-refundable</strong>.
						If you are a consumer residing in the European Union, you have the right to withdraw from
						your subscription within fourteen (14) days from the date of your subscription without
						providing a reason. To exercise your right to withdraw, you must notify us (e.g., at
						<a href="mailto:operations@keepersolutions.com" class="text-hg-blue hover:underline"
							>operations@keepersolutions.com</a
						>) of your decision to cancel within this withdrawal period. If you withdraw in
						accordance with this right, we will refund all payments made by you without undue delay
						and in any event not later than fourteen (14) days from the day on which we are informed
						about your decision to withdraw, using the same means of payment as you used for the
						initial transaction.
					</p>
				</div>
			</div>
		</section>

		<!-- Section 6 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				6. Fair Use Policy
			</h2>
			<div class="space-y-6 text-hg-text-secondary leading-relaxed">
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">6.1 Purpose</h3>
					<p>
						Each subscription plan is priced to reflect a <strong class="text-hg-text-primary">reasonable and expected level of usage</strong>
						for an individual user or team. The monthly credit allowance included in your plan represents
						your fair use entitlement for that billing period.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">6.2 How Credits Work</h3>
					<p class="mb-2">
						Your monthly subscription fee includes a pre-defined credit allowance. As you make LLM
						queries, credits are deducted in real time based on the model used and the volume of
						tokens consumed. Your remaining credit balance is always visible in your account
						dashboard.
					</p>
					<p>
						Different LLM models have different per-token costs. Higher-capability models (e.g.
						GPT-4, Claude Opus) consume credits more quickly than lighter models (e.g. Claude Haiku,
						GPT-4o mini). This is displayed transparently on the Platform's pricing and model
						selection pages.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">
						6.3 What Happens When Your Credit Allowance Is Exhausted
					</h3>
					<p class="mb-2">
						Your subscription fee covers usage <strong class="text-hg-text-primary">up to your plan's monthly credit limit</strong>.
						If you exhaust your credit allowance before the end of your billing period:
					</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>
							<strong class="text-hg-text-primary">Your access to LLM queries will be paused automatically</strong>
							unless you are on a Premium plan with auto top-up enabled.
						</li>
						<li>
							You will receive a clear in-platform notification and email alert explaining that your
							allowance has been reached.
						</li>
						<li>
							You will be presented with options to <strong class="text-hg-text-primary">purchase additional credit</strong>
							to continue using the Platform immediately, or to
							<strong class="text-hg-text-primary">wait until your next billing cycle</strong> when your allowance resets.
						</li>
					</ul>
					<p class="mt-2">
						<strong class="text-hg-text-primary">Continued use beyond your plan's credit limit is not included in your subscription fee and will incur additional charges.</strong>
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">
						6.4 Purchasing Additional Credit (Top-Ups)
					</h3>
					<p>
						If you wish to continue using the Platform after exhausting your monthly allowance, you
						may purchase additional credit top-ups. Top-up charges are processed immediately via
						your stored payment method through Stripe. You will receive a receipt for each top-up
						purchase.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">6.5 Auto Top-Up (Premium Plan)</h3>
					<p class="mb-2">
						If you are on the <strong class="text-hg-text-primary">Premium plan</strong> and have
						enabled auto top-up:
					</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>
							Additional credit will be charged automatically in agreed increments whenever your
							balance reaches zero.
						</li>
						<li>You will receive an <strong class="text-hg-text-primary">email notification for each automatic charge</strong>.</li>
						<li>
							You may set a <strong class="text-hg-text-primary">monthly hard cap</strong> on total
							top-up spend via your account or team admin settings.
						</li>
						<li>
							You may disable auto top-up at any time from your account settings, after which any
							future exhaustion of credit will follow the standard paused-access flow.
						</li>
					</ul>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">
						6.6 Notifications & Transparency
					</h3>
					<p class="mb-2">
						We are committed to giving you clear visibility of your usage at all times. In your
						account dashboard, you will always be able to see:
					</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>Your <strong class="text-hg-text-primary">current credit balance</strong>.</li>
						<li>Your <strong class="text-hg-text-primary">total spend to date</strong> this billing period.</li>
						<li>A <strong class="text-hg-text-primary">per-model token breakdown</strong> showing tokens consumed and associated cost.</li>
						<li>Your <strong class="text-hg-text-primary">plan's monthly credit allowance</strong>.</li>
					</ul>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">6.7 Misuse & Credit Abuse</h3>
					<p class="mb-2">
						The following behaviours are considered a violation of this Fair Use Policy and may
						result in suspension or termination of your account:
					</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>Attempting to <strong class="text-hg-text-primary">circumvent credit limits</strong> through technical means, account manipulation, or creation of multiple accounts to claim repeated free trial credits.</li>
						<li>Using the Platform for <strong class="text-hg-text-primary">high-volume automated querying</strong> beyond what is reasonably consistent with your plan level, without prior agreement with Keeper Solutions.</li>
						<li>Using the Platform in a manner that <strong class="text-hg-text-primary">disrupts service availability</strong> for other users.</li>
					</ul>
				</div>
			</div>
		</section>

		<!-- Section 7 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				7. Acceptable Use
			</h2>
			<div class="space-y-6 text-hg-text-secondary leading-relaxed">
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">7.1 Permitted Use</h3>
					<p>
						You may use the Platform for lawful business and personal purposes, including but not
						limited to drafting, research, summarisation, coding assistance, and analysis.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">7.2 Prohibited Use</h3>
					<p class="mb-2">You must not use the Platform in the following ways:</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>Generate or distribute <strong class="text-hg-text-primary">illegal content</strong>, including content that promotes violence, hatred, discrimination, or exploitation of minors.</li>
						<li>Engage in <strong class="text-hg-text-primary">fraud, deception, or misrepresentation</strong>, including the generation of fake reviews, phishing content, or impersonation.</li>
						<li>Violate any <strong class="text-hg-text-primary">intellectual property rights</strong> of third parties.</li>
						<li>Attempt to <strong class="text-hg-text-primary">reverse engineer, scrape, or extract</strong> the underlying models or the Platform infrastructure.</li>
						<li><strong class="text-hg-text-primary">Circumvent billing controls</strong>, including attempts to artificially reset or abuse free trial credits.</li>
						<li>Transmit <strong class="text-hg-text-primary">malware</strong>, viruses, or any code designed to disrupt, damage, or gain unauthorised access to any system.</li>
						<li>Use the Platform in connection with <strong class="text-hg-text-primary">automated bulk querying</strong> unless expressly permitted under your plan.</li>
						<li>Use the Platform to generate <strong class="text-hg-text-primary">regulated financial advice, medical advice, or legal advice</strong> intended to be relied upon without appropriate professional review.</li>
						<li>Use the Platform, or its underlying models, to develop, train, or improve any foundation models, large language models, or other large-scale models that compete with HubGate or its third-party AI provider.</li>
						<li>Violate applicable laws or regulations in your jurisdiction.</li>
					</ul>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">
						7.3 Responsibility for Prompts and Outputs
					</h3>
					<p class="mb-2">You are solely responsible for:</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>The content of prompts you submit to the Platform.</li>
						<li>Your use of any outputs generated by the Platform.</li>
						<li>Ensuring that your use complies with applicable laws, including data protection, copyright, and sector-specific regulations.</li>
					</ul>
					<p class="mt-2">
						LLM outputs are <strong class="text-hg-text-primary">probabilistic and may be inaccurate, incomplete, or misleading</strong>.
						You should exercise independent judgement before relying on any AI-generated output for
						business decisions. Outputs are not authoritative or infallible sources of information.
						You should not rely on the Platform during emergencies, and you must not rely on
						AI-generated output as the sole source of truth or as a substitute for professional
						advice.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">
						7.4 Reporting Content & Moderation
					</h3>
					<p>
						We reserve the right to review, restrict access to, or disable content at any time if
						it violates applicable law or these Terms. If you believe any content on the Platform
						constitutes illegal content or violates intellectual property rights, you may notify us
						at
						<a href="mailto:operations@keepersolutions.com" class="text-hg-blue hover:underline"
							>operations@keepersolutions.com</a
						>.
					</p>
				</div>
			</div>
		</section>

		<!-- Section 8 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">8. Data & Privacy</h2>
			<div class="space-y-6 text-hg-text-secondary leading-relaxed">
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">8.1 Personal Data</h3>
					<p>
						Your use of the Platform is also governed by our
						<a href="/privacy" class="text-hg-blue hover:underline">Privacy Policy</a>, which is
						incorporated into these Terms by reference. Please review it carefully.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">8.2 Prompt Data</h3>
					<p>
						We process your queries in order to route them to the selected LLM provider. Queries
						are also used for <strong class="text-hg-text-primary">usage metering and billing purposes</strong>.
						We do not use your prompt content to train our own models. Please be aware that queries
						are transmitted to <strong class="text-hg-text-primary">third-party LLM providers</strong>
						(e.g. OpenAI, Anthropic, Google). Their terms and privacy policies apply to the processing
						they perform. You should
						<strong class="text-hg-text-primary">avoid submitting sensitive personal data, confidential business information, or regulated data</strong>
						through the Platform unless you have independently verified the data handling practices of
						the relevant LLM provider.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">8.3 Analytics & Feedback</h3>
					<p>
						The Platform uses <strong class="text-hg-text-primary">Google Analytics</strong> to
						understand usage patterns and improve the service. We may also use tools to collect user
						feedback. Data collected through these tools is subject to the respective providers'
						privacy policies.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">8.4 Data Retention</h3>
					<p>
						Account data and usage records are retained for as long as your account is active and
						for a reasonable period thereafter for billing, audit, and legal compliance purposes.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">8.5 Data Export and Switching</h3>
					<p>
						You have the right to switch to another service provider or your own infrastructure.
						You may export your data and digital assets (such as prompts and conversation outputs)
						prior to terminating your account. Once you have exported your data, we will retain it
						for a retrieval period of at least thirty (30) days. Once you have notified us of a
						successful switch, HubGate will permanently erase all your data and digital assets,
						unless retention is required under applicable law.
					</p>
				</div>
			</div>
		</section>

		<!-- Section 9 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				9. Intellectual Property
			</h2>
			<div class="space-y-6 text-hg-text-secondary leading-relaxed">
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">9.1 Platform IP</h3>
					<p>
						All intellectual property rights in the Platform, including its design, software, and
						documentation, are owned by or licensed to Keeper Solutions. Nothing in these Terms
						grants you any ownership interest in the Platform.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">9.2 Your Content</h3>
					<p>
						You retain ownership of the prompts you submit and any outputs you generate. You grant
						Keeper Solutions a limited, non-exclusive licence to process your prompts solely for the
						purpose of delivering the service.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">9.3 Feedback</h3>
					<p>
						If you provide feedback or suggestions about the Platform, you grant us a royalty-free,
						perpetual licence to use that feedback for any purpose without obligation to you.
					</p>
				</div>
			</div>
		</section>

		<!-- Section 10 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				10. Availability & Service Levels
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					We aim to provide a reliable and high-performance service. However, we do not guarantee
					uninterrupted availability. The Platform may experience downtime due to:
				</p>
				<ul class="list-disc pl-6 space-y-1">
					<li>Scheduled maintenance (we will endeavour to provide advance notice).</li>
					<li>Unplanned outages, including issues with third-party LLM providers.</li>
					<li>Events beyond our reasonable control.</li>
				</ul>
				<p>
					Where a third-party LLM provider experiences an outage, your ability to use certain
					models may be affected. We will endeavour to maintain access to alternative models where
					possible.
				</p>
			</div>
		</section>

		<!-- Section 11 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				11. Third-Party LLM Providers
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					The Platform acts as a gateway to third-party AI model providers. Keeper Solutions does
					not control, endorse, or warrant the outputs, availability, or conduct of those
					providers. By using the Platform, you acknowledge that:
				</p>
				<ul class="list-disc pl-6 space-y-1">
					<li>The underlying AI models are operated by third parties.</li>
					<li>Those providers may change their models, APIs, or terms at any time.</li>
					<li>Keeper Solutions may add or remove available models with reasonable notice.</li>
				</ul>
			</div>
		</section>

		<!-- Section 12 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				12. Disclaimer of Warranties
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					<strong class="text-hg-text-primary">To the fullest extent permitted by applicable law</strong>,
					the Platform is provided <strong class="text-hg-text-primary">"as is"</strong> and
					<strong class="text-hg-text-primary">"as available"</strong> without warranty of any kind.
					Keeper Solutions expressly disclaims:
				</p>
				<ul class="list-disc pl-6 space-y-1">
					<li>Any warranty of merchantability, fitness for a particular purpose, or non-infringement.</li>
					<li>Any warranty that the Platform will be error-free, uninterrupted, or secure.</li>
					<li>Any warranty as to the accuracy, reliability, or completeness of any LLM output.</li>
				</ul>
				<p>
					Nothing in these Terms affects your rights under applicable consumer protection
					legislation. If you are a consumer residing in the European Economic Area (EEA), you may
					also benefit from statutory legal warranties of conformity for digital services under the
					mandatory laws of your country of residence.
				</p>
			</div>
		</section>

		<!-- Section 13 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				13. Limitation of Liability
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					<strong class="text-hg-text-primary">To the fullest extent permitted by applicable law</strong>,
					Keeper Solutions shall not be liable for:
				</p>
				<ul class="list-disc pl-6 space-y-1">
					<li>Any indirect, incidental, consequential, or punitive damages arising out of your use of, or inability to use, the Platform.</li>
					<li>Any loss of revenue, profit, data, or business opportunity.</li>
					<li>Any reliance on AI-generated outputs for business, financial, legal, medical, or other decisions.</li>
					<li>Any interruption to service caused by exhaustion of your credit allowance where adequate notifications were provided.</li>
				</ul>
				<p>
					Where liability cannot be excluded by law, our total aggregate liability to you shall not
					exceed the <strong class="text-hg-text-primary">greater of (a) the amounts paid by you to Keeper Solutions in the 12 months preceding the claim, or (b) €100 EUR</strong>.
				</p>
				<p>
					Nothing in these Terms limits liability for <strong class="text-hg-text-primary">death or personal injury caused by our negligence</strong>,
					<strong class="text-hg-text-primary">fraud</strong>, or any other liability that cannot
					lawfully be excluded.
				</p>
			</div>
		</section>

		<!-- Section 14 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				14. Indemnification
			</h2>
			<p class="text-hg-text-secondary leading-relaxed">
				You agree to indemnify and hold harmless Keeper Solutions, its directors, employees, and
				contractors from any claims, losses, damages, or expenses (including reasonable legal fees)
				arising from: your use of the Platform in violation of these Terms; your violation of any
				applicable law or third-party rights; or content or prompts you submit through the Platform.
			</p>
		</section>

		<!-- Section 15 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">15. Termination</h2>
			<div class="space-y-6 text-hg-text-secondary leading-relaxed">
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">15.1 By You</h3>
					<p>
						You may close your account at any time via your account settings. Subscription
						cancellations are subject to Section 5.8 above.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">15.2 By Us</h3>
					<p class="mb-2">
						We reserve the right to <strong class="text-hg-text-primary">suspend or terminate</strong>
						your account with or without notice if:
					</p>
					<ul class="list-disc pl-6 space-y-1">
						<li>You breach these Terms or the Fair Use Policy set out in Section 6.</li>
						<li>We are required to do so by law or regulatory authority.</li>
						<li>You engage in conduct that we reasonably believe poses a risk to the Platform, other users, or third parties.</li>
						<li>Outstanding payment obligations remain unresolved after reasonable notice and retry attempts.</li>
					</ul>
					<p class="mt-2">
						On termination, your right to access the Platform ceases immediately. Any outstanding
						balance owed to us remains due.
					</p>
				</div>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">15.3 Survival</h3>
					<p>
						Upon termination or expiration of these Terms, the provisions that are intended by
						their nature to survive termination will continue in full force and effect, including
						payment obligations, intellectual property rights, limitations of liability, and
						indemnification.
					</p>
				</div>
			</div>
		</section>

		<!-- Section 16 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				16. Changes to These Terms
			</h2>
			<p class="text-hg-text-secondary leading-relaxed">
				We may update these Terms from time to time. We will notify you of material changes by
				email or via a notice on the Platform at least <strong class="text-hg-text-primary">14 days before</strong>
				the changes take effect. Your continued use of the Platform after that date constitutes acceptance
				of the updated Terms. If you do not accept the changes, you may cancel your account before the
				effective date.
			</p>
		</section>

		<!-- Section 17 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				17. Governing Law & Dispute Resolution
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					These Terms are governed by the laws of <strong class="text-hg-text-primary">Ireland</strong>.
					Any disputes arising out of or in connection with these Terms shall be subject to the
					<strong class="text-hg-text-primary">exclusive jurisdiction of the courts of Ireland</strong>,
					without prejudice to any mandatory consumer protection rights you may have under the law
					of your country of residence within the EU.
				</p>
				<p>
					If you are an EU consumer, you retain the right to initiate a voluntary mediation process
					in the event of a dispute. Participation in mediation is voluntary, and either party may
					withdraw from the process at any time without prejudice to their legal rights.
				</p>
			</div>
		</section>

		<!-- Section 18 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">18. General</h2>
			<ul class="space-y-3 text-hg-text-secondary leading-relaxed">
				<li>
					<strong class="text-hg-text-primary">Entire Agreement:</strong> These Terms, together with
					our Privacy Policy, constitute the entire agreement between you and Keeper Solutions regarding
					the Platform.
				</li>
				<li>
					<strong class="text-hg-text-primary">Severability:</strong> If any provision of these Terms
					is found to be unenforceable, the remaining provisions continue in full force.
				</li>
				<li>
					<strong class="text-hg-text-primary">No Waiver:</strong> Our failure to enforce any provision
					does not constitute a waiver of our right to enforce it in the future.
				</li>
				<li>
					<strong class="text-hg-text-primary">Assignment:</strong> You may not assign your rights under
					these Terms without our prior written consent. We may assign our rights to any successor entity.
				</li>
				<li>
					<strong class="text-hg-text-primary">Force Majeure:</strong> Neither party will be liable to
					the other for any delay or failure to perform any obligation under these Terms (except for a
					failure to pay fees) if such delay or failure is due to unforeseen events beyond the reasonable
					control of such party.
				</li>
			</ul>
		</section>

		<!-- Section 19 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">19. Contact</h2>
			<div class="text-hg-text-secondary leading-relaxed">
				<p class="font-semibold text-hg-text-primary mb-2">Keeper Solutions</p>
				<p>
					Email:
					<a href="mailto:operations@keepersolutions.com" class="text-hg-blue hover:underline"
						>operations@keepersolutions.com</a
					>
				</p>
				<p>
					Web:
					<a
						href="https://hubgate.io"
						target="_blank"
						rel="noopener noreferrer"
						class="text-hg-blue hover:underline">hubgate.io</a
					>
				</p>
			</div>
		</section>
	</main>

	<HgLandingFooter />
</div>
