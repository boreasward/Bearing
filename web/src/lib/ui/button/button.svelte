<script lang="ts" module>
	import { type VariantProps, tv } from "tailwind-variants";
	import { cn, type WithElementRef } from "$lib/utils.js";
	import type { HTMLAnchorAttributes, HTMLButtonAttributes } from "svelte/elements";

	export const buttonVariants = tv({
		base: "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-transparent text-sm font-medium transition-colors select-none disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
		variants: {
			variant: {
				primary: "bg-primary text-primary-foreground hover:bg-primary/90",
				outline: "border-input bg-card text-foreground hover:bg-muted",
				ghost: "text-foreground hover:bg-muted",
				link: "text-primary underline underline-offset-4 hover:text-primary/80",
				destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
			},
			size: {
				sm: "h-7 px-2.5",
				md: "h-8 px-3",
				icon: "size-7",
			},
		},
		compoundVariants: [
			{ variant: "link", size: ["sm", "md"], class: "h-auto px-0" },
		],
		defaultVariants: {
			variant: "outline",
			size: "md",
		},
	});

	export type ButtonVariant = VariantProps<typeof buttonVariants>["variant"];
	export type ButtonSize = VariantProps<typeof buttonVariants>["size"];

	export type ButtonProps = WithElementRef<HTMLButtonAttributes> &
		WithElementRef<HTMLAnchorAttributes> & {
			variant?: ButtonVariant;
			size?: ButtonSize;
			/** Disables the button, sets aria-busy and shows a spinner. */
			busy?: boolean;
			/** Replaces the label while busy. */
			busyText?: string;
		};
</script>

<script lang="ts">
	import LoaderCircleIcon from "@lucide/svelte/icons/loader-circle";

	let {
		class: className,
		variant = "outline",
		size = "md",
		ref = $bindable(null),
		href = undefined,
		type = "button",
		disabled,
		busy = false,
		busyText,
		children,
		...restProps
	}: ButtonProps = $props();

	const inactive = $derived(Boolean(disabled) || busy);
</script>

{#snippet content()}
	{#if busy}
		<LoaderCircleIcon class="animate-spin" aria-hidden="true" />
	{/if}
	{#if busy && busyText}
		{busyText}
	{:else}
		{@render children?.()}
	{/if}
{/snippet}

{#if href}
	<a
		bind:this={ref}
		data-slot="button"
		class={cn(buttonVariants({ variant, size }), className)}
		href={inactive ? undefined : href}
		aria-disabled={inactive ? true : undefined}
		aria-busy={busy ? "true" : undefined}
		role={inactive ? "link" : undefined}
		tabindex={inactive ? -1 : undefined}
		{...restProps}
	>
		{@render content()}
	</a>
{:else}
	<button
		bind:this={ref}
		data-slot="button"
		class={cn(buttonVariants({ variant, size }), className)}
		{type}
		disabled={inactive}
		aria-busy={busy ? "true" : undefined}
		{...restProps}
	>
		{@render content()}
	</button>
{/if}
