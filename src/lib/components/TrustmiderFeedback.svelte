<script lang="ts">
	import { onMount, onDestroy } from 'svelte';

	const baseUrl = 'https://my-feedback.is';
	const path = '/genny/6a30065898cc45120a0e032c';
	const iconPath =
		'M20 2H4C2.9 2 2 2.9 2 4V22L6 18H20C21.1 18 22 17.1 22 16V4C22 2.9 21.1 2 20 2M20 16H5.2L4 17.2V4H20V16Z';
	const buttonColor = '#2563eb';

	let button: HTMLButtonElement;
	let popup: HTMLDivElement;
	let iframe: HTMLIFrameElement;

	function closePopup() {
		popup.style.transform = 'scale(0.95)';
		popup.style.opacity = '0';
		setTimeout(() => {
			popup.style.display = 'none';
		}, 300);
	}

	function handleClickOutside(e: MouseEvent) {
		if (
			popup.style.display === 'block' &&
			!popup.contains(e.target as Node) &&
			!button.contains(e.target as Node)
		) {
			closePopup();
		}
	}

	function handleButtonClick() {
		if (popup.style.display === 'block') {
			closePopup();
		} else {
			popup.style.display = 'block';
			setTimeout(() => {
				popup.style.transform = 'scale(1)';
				popup.style.opacity = '1';
			}, 10);

			iframe.src = baseUrl + path;
		}
	}

	onMount(() => {
		window.addEventListener('mousedown', handleClickOutside, true);
	});

	onDestroy(() => {
		window.removeEventListener('mousedown', handleClickOutside, true);
	});
</script>

<button
	bind:this={button}
	on:click={handleButtonClick}
	on:mouseover={(e) => (e.currentTarget.style.transform = 'scale(1.1)')}
	on:mouseout={(e) => (e.currentTarget.style.transform = 'scale(1)')}
	on:focus={() => {}}
	on:blur={() => {}}
	aria-label="Open feedback form"
	style="position:fixed; bottom:20px; right:20px; width:60px; height:60px; border-radius:50%; background-color:{buttonColor}; border:none; cursor:pointer; display:flex; align-items:center; justify-content:center; box-shadow:0 2px 8px rgba(0,0,0,0.2); z-index:9999; transition:transform 0.2s;"
>
	<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white" width="24" height="24">
		<path d={iconPath} />
	</svg>
</button>

<div
	bind:this={popup}
	style="position:fixed; bottom:90px; right:20px; width:400px; height:600px; background:white; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.15); z-index:9998; display:none; overflow:hidden; transition:all 0.3s ease; transform:scale(0.95); opacity:0;"
>
	<button
		on:click={(e) => {
			e.stopPropagation();
			closePopup();
		}}
		on:mouseover={(e) => (e.currentTarget.style.backgroundColor = 'rgba(0,0,0,0.1)')}
		on:mouseout={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
		aria-label="Close feedback form"
		style="position:absolute; top:8px; right:8px; width:32px; height:32px; border:none; background:transparent; border-radius:50%; cursor:pointer; display:flex; align-items:center; justify-content:center; z-index:1; transition:background-color 0.2s;"
	>
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#666" width="20" height="20">
			<path
				d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"
			/>
		</svg>
	</button>

	<iframe bind:this={iframe} title="Feedback survey" style="width:100%; height:100%; border:none;"
	></iframe>
</div>
