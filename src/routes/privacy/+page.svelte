<script lang="ts">
	import HgLandingHeader from '$lib/components/hubgate/HgLandingHeader.svelte';
	import HgLandingFooter from '$lib/components/hubgate/HgLandingFooter.svelte';
</script>

<svelte:head>
	<title>Privacy Policy — Hubgate</title>
</svelte:head>

<div class="min-h-screen flex flex-col font-hg-body bg-hg-bg-surface">
	<HgLandingHeader />

	<main class="flex-1 max-w-3xl mx-auto w-full px-6 py-16">
		<div class="mb-10">
			<h1 class="font-hg-heading font-bold text-3xl text-hg-text-primary mb-2">
				HubGate Privacy Policy
			</h1>
		</div>

		<div class="space-y-4 text-hg-text-secondary leading-relaxed mb-10">
			<p>
				This Privacy Policy explains how HubGate collects, uses, shares, and protects data when
				providing our AI Gateway platform. Because HubGate sits between users and multiple
				third-party AI providers, this policy details exactly what happens to your data at each
				stage of the pipeline.
			</p>
			<p>
				For B2B customers, our Data Processing Agreement (DPA) is embedded directly within
				Section 8.4 of our
				<a href="/terms" class="text-hg-blue hover:underline">Terms of Use</a>.
			</p>
		</div>

		<hr class="border-hg-border mb-10" />

		<!-- Section 1 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				1. Who is the Data Controller?
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					HubGate acts as the <strong class="text-hg-text-primary">Data Controller</strong> for account
					and billing information. HubGate acts as a
					<strong class="text-hg-text-primary">Data Processor</strong> on behalf of customer organisations
					for user prompts, uploaded files, and connected knowledge sources.
				</p>
				<ul class="list-disc pl-6 space-y-2">
					<li><strong class="text-hg-text-primary">Legal Entity:</strong> Keeper Technology Solutions Ltd.</li>
					<li>
						<strong class="text-hg-text-primary">Registered Address:</strong> Keeper House, Oakhampton,
						Newport, Co. Tipperary, Ireland
					</li>
					<li><strong class="text-hg-text-primary">Company Registration Number:</strong> 502039</li>
					<li>
						<strong class="text-hg-text-primary">Data Protection Contact:</strong>
						<a href="mailto:privacy@keepersolutions.com" class="text-hg-blue hover:underline"
							>privacy@keepersolutions.com</a
						>
					</li>
				</ul>
			</div>
		</section>

		<!-- Section 2 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				2. Categories of Data Collected
			</h2>
			<div class="text-hg-text-secondary leading-relaxed">
				<p class="mb-3">HubGate handles the following categories of data:</p>
				<ul class="list-disc pl-6 space-y-2">
					<li>
						<strong class="text-hg-text-primary">Account Data:</strong> Name, email address, organisation,
						user role, and login history.
					</li>
					<li>
						<strong class="text-hg-text-primary">Billing Data:</strong> Subscription information, usage
						metrics, and payment records.
					</li>
					<li>
						<strong class="text-hg-text-primary">AI Interaction Data:</strong> Prompts, uploaded documents,
						AI outputs, agent interactions, and conversation history.
					</li>
					<li>
						<strong class="text-hg-text-primary">Knowledge Source Data:</strong> Content from connected
						corporate repositories (e.g., Google Drive, SharePoint, Confluence, Notion).
					</li>
					<li>
						<strong class="text-hg-text-primary">Technical Data:</strong> IP address, browser type, device
						information, and session identifiers.
					</li>
					<li>
						<strong class="text-hg-text-primary">Audit Data:</strong> User actions, prompt timestamps,
						model selection, token consumption, and administrative actions.
					</li>
				</ul>
			</div>
		</section>

		<!-- Section 3 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				3. Purpose of Processing
			</h2>
			<div class="text-hg-text-secondary leading-relaxed">
				<p class="mb-3">We process the collected data for specific operational purposes:</p>
				<ul class="list-disc pl-6 space-y-2">
					<li><strong class="text-hg-text-primary">Account Data:</strong> For user authentication and account management.</li>
					<li><strong class="text-hg-text-primary">Usage Data:</strong> For billing and cost reporting (token usage).</li>
					<li><strong class="text-hg-text-primary">Prompts &amp; Interactions:</strong> For AI routing to selected third-party models.</li>
					<li><strong class="text-hg-text-primary">Connected Repositories:</strong> For knowledge retrieval and augmented generation.</li>
					<li><strong class="text-hg-text-primary">Logs &amp; Audit Records:</strong> For security monitoring, compliance, and platform auditing.</li>
				</ul>
			</div>
		</section>

		<!-- Section 4 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				4. Legal Basis Under GDPR
			</h2>
			<div class="text-hg-text-secondary leading-relaxed">
				<p class="mb-3">We rely on the following lawful bases to process your data:</p>
				<ul class="list-disc pl-6 space-y-2">
					<li><strong class="text-hg-text-primary">Contract:</strong> For account management, providing AI services, and billing.</li>
					<li><strong class="text-hg-text-primary">Legitimate Interest:</strong> For security monitoring, fraud prevention, and service improvement.</li>
					<li><strong class="text-hg-text-primary">Consent:</strong> For marketing emails and optional analytics.</li>
					<li><strong class="text-hg-text-primary">Legal Obligation:</strong> For tax records and responding to law enforcement requests.</li>
				</ul>
			</div>
		</section>

		<!-- Section 5 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				5. AI Model Providers
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					User prompts and queries are transmitted to third-party AI providers selected by the
					customer organisation. These providers may include OpenAI, Anthropic, Google, and
					Perplexity.
				</p>
				<ul class="list-disc pl-6 space-y-2">
					<li>
						<strong class="text-hg-text-primary">What is sent:</strong> Only the specific prompts and
						necessary contextual data required to generate an output.
					</li>
					<li>
						<strong class="text-hg-text-primary">Training limitations:</strong> We do not use your prompt
						content to train our own models.
					</li>
					<li>
						<strong class="text-hg-text-primary">Provider Policies:</strong> The respective terms and
						privacy policies of the selected third-party providers apply to your queries once
						transmitted.
					</li>
				</ul>
				<p>
					For avoidance of doubt, Keeper Solutions' commitment not to share Customer Company or
					Business data with third parties applies to data processed within our core development,
					consulting, and operational services. Products such as HubGate operate using a separate
					data-processing workflow in which user-submitted interaction data (including prompts,
					queries, and connected content) is intentionally transmitted to third-party AI model
					providers chosen by the customer. This transmission is essential to the functioning of
					the HubGate service and is carried out strictly under contractual, security, and
					compliance controls.
				</p>
			</div>
		</section>

		<!-- Section 6 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">6. PII Filtering</h2>
			<p class="text-hg-text-secondary leading-relaxed">
				Prior to transmission to external AI providers, HubGate may analyse prompts and uploaded
				content to identify and mask Personally Identifiable Information (PII). While this
				technology reduces the risk of sensitive data exposure, it is not a guarantee of perfect
				protection.
			</p>
		</section>

		<!-- Section 7 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">7. Audit Logging</h2>
			<div class="text-hg-text-secondary leading-relaxed">
				<p class="mb-3">To maintain a secure corporate AI gateway, HubGate maintains detailed audit logs.</p>
				<ul class="list-disc pl-6 space-y-2">
					<li><strong class="text-hg-text-primary">What is logged:</strong> Prompts, AI outputs, timestamps, model selection, and user IDs.</li>
					<li><strong class="text-hg-text-primary">Why it is logged:</strong> For organisational security, compliance tracking, and usage auditing.</li>
					<li><strong class="text-hg-text-primary">Visibility:</strong> Organisation administrators have access to these logs.</li>
				</ul>
			</div>
		</section>

		<!-- Section 8 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				8. Connected Data Sources
			</h2>
			<div class="space-y-6 text-hg-text-secondary leading-relaxed">
				<p>
					If your organisation connects HubGate to external data sources (e.g., Google Drive,
					SharePoint, Notion, Dropbox), we will request specific permissions to index this content.
					Users and administrators can manage or disconnect this access at any time.
				</p>
				<div>
					<h3 class="font-semibold text-hg-text-primary mb-2">8.1 Google Drive Data Handling</h3>
					<p class="mb-2">
						When you connect Google Drive to HubGate, we access, transmit, and store your Google
						Drive data solely to provide and improve the user-facing features of the Platform
						(search, retrieval, and AI-assisted file management), consistent with Google's API
						Services User Data Policy, including its Limited Use requirements.
					</p>
					<ul class="list-disc pl-6 space-y-2">
						<li>
							<strong class="text-hg-text-primary">No sale or transfer to third parties.</strong>
							We do not sell, rent, or transfer data accessed through Google Drive to advertising
							platforms, data brokers, or other information resellers.
						</li>
						<li>
							<strong class="text-hg-text-primary">No use for advertising.</strong> We do not use
							Google Drive data for serving advertisements of any kind, including retargeting,
							personalised, or interest-based advertising.
						</li>
						<li>
							<strong class="text-hg-text-primary">Human access.</strong> Employees or contractors of
							HubGate do not access the content of your Google Drive files except: (a) with your
							affirmative consent (e.g., when you contact support about a specific issue), (b) where
							necessary to maintain the security of the Platform, such as investigating abuse or a
							security incident, or (c) where required to comply with applicable law.
						</li>
						<li>
							<strong class="text-hg-text-primary">Scope of access.</strong> HubGate only accesses
							files and actions you have authorised through the Google OAuth consent screen, and you
							may revoke this access at any time from your Google Account permissions or from
							HubGate's Connectors settings.
						</li>
					</ul>
				</div>
			</div>
		</section>

		<!-- Section 9 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				9. RAG and Embeddings
			</h2>
			<p class="text-hg-text-secondary leading-relaxed">
				To facilitate knowledge retrieval, HubGate creates vector embeddings of your connected
				data. These embeddings are generated and stored securely on our infrastructure and will
				remain until the source is deleted or disconnected.
			</p>
		</section>

		<!-- Section 10 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				10. International Transfers
			</h2>
			<p class="text-hg-text-secondary leading-relaxed">
				Using US-based LLMs (such as OpenAI, Anthropic, or Google) requires the international
				transfer of data. Where personal data is transferred outside the EU/EEA, Keeper Solutions
				ensures appropriate safeguards are in place, such as Standard Contractual Clauses (SCCs)
				and adequacy decisions.
			</p>
		</section>

		<!-- Section 11 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">11. Data Retention</h2>
			<div class="text-hg-text-secondary leading-relaxed">
				<p class="mb-3">We do not keep data indefinitely. Data is retained according to the following schedule:</p>
				<ul class="list-disc pl-6 space-y-2">
					<li><strong class="text-hg-text-primary">Accounts:</strong> 1 year after account closure.</li>
					<li><strong class="text-hg-text-primary">Audit logs:</strong> 2 years.</li>
					<li><strong class="text-hg-text-primary">Conversations:</strong> 3 months.</li>
					<li><strong class="text-hg-text-primary">Billing records:</strong> 7 years.</li>
					<li><strong class="text-hg-text-primary">Embeddings:</strong> Until deletion or disconnection of the source.</li>
				</ul>
			</div>
		</section>

		<!-- Section 12 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				12. Customer Administrator Access
			</h2>
			<p class="text-hg-text-secondary leading-relaxed">
				Important notice for organisational users: HubGate is designed for corporate governance.
				Organisation administrators may have access to usage metrics, logs, prompts, outputs, and
				audit records generated by users within their organisation.
			</p>
		</section>

		<!-- Section 13 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				13. Automated Decision Making
			</h2>
			<p class="text-hg-text-secondary leading-relaxed">
				HubGate does not make legally significant automated decisions about individuals.
			</p>
		</section>

		<!-- Section 14 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				14. Security Measures
			</h2>
			<div class="space-y-4 text-hg-text-secondary leading-relaxed">
				<p>
					We maintain appropriate technical and organisational security measures throughout our
					development lifecycle. This includes encryption in transit and at rest, strict access
					controls, role-based permissions, and continuous security monitoring.
				</p>
				<p>
					Crucially, HubGate utilizes an advanced, application-layer AES-256-GCM encryption system
					for all chat messages. This ensures that chat content is encrypted inside the
					application process before it ever reaches our database. Because the database only
					stores opaque ciphertext, even a database administrator with direct system access cannot
					read your chat logs in plaintext.
				</p>
			</div>
		</section>

		<!-- Section 15 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">15. GDPR Rights</h2>
			<div class="text-hg-text-secondary leading-relaxed">
				<p class="mb-3">Under the GDPR, you have the right to:</p>
				<ul class="list-disc pl-6 space-y-2">
					<li>Access, rectify, or erase your personal data.</li>
					<li>Restrict or object to processing.</li>
					<li>Data portability.</li>
					<li>Lodge a complaint with a supervisory authority (in Ireland, this is the Data Protection Commission).</li>
				</ul>
				<p class="mt-3">
					To exercise your rights, please contact
					<a href="mailto:privacy@keepersolutions.com" class="text-hg-blue hover:underline"
						>privacy@keepersolutions.com</a
					>.
				</p>
			</div>
		</section>

		<!-- Section 16 -->
		<section class="mb-10">
			<h2 class="font-hg-heading font-bold text-xl text-hg-text-primary mb-4">
				16. How HubGate Uses Artificial Intelligence
			</h2>
			<ul class="list-disc pl-6 space-y-2 text-hg-text-secondary leading-relaxed">
				<li>
					<strong class="text-hg-text-primary">Routing:</strong> Requests are routed to the specific AI
					provider (e.g., OpenAI, Anthropic) selected by the user or organisation.
				</li>
				<li>
					<strong class="text-hg-text-primary">Training:</strong> HubGate does not use your prompts or
					outputs to train our own models.
				</li>
				<li>
					<strong class="text-hg-text-primary">Limitations:</strong> LLM outputs are probabilistic and
					may be inaccurate. Users should not rely on them for medical, legal, or financial advice.
				</li>
			</ul>
		</section>
	</main>

	<HgLandingFooter />
</div>
