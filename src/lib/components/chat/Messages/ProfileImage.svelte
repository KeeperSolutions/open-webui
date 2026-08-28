<script lang="ts">
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { safeImageUrl } from '$lib/utils/safeImageUrl';

	export let className = 'size-8';
	export let src = `${WEBUI_BASE_URL}/static/favicon.png`;
</script>

<img
	aria-hidden="true"
	src={safeImageUrl(src)}
<<<<<<< HEAD
	class=" {className} object-cover rounded-full"
=======
	class=" {className} object-cover rounded-2xl"
>>>>>>> v0.11.0
	alt="profile"
	draggable="false"
	loading="lazy"
/>
