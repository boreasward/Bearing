export type ContextChip = { kind: 'ticker' | 'plan' | 'research'; ticker: string; id: number | null }
export const BRING_EVENT = 'workbench:bring'
export const ACTION_EVENT = 'workbench:action'
export type BringEvent = CustomEvent<ContextChip>
export type ActionEvent = CustomEvent<{ text: string }>
export function bringToChat(chip: ContextChip): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent<ContextChip>(BRING_EVENT, { detail: chip }))
}
export function actionCompleted(text: string): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(ACTION_EVENT, { detail: { text } }))
}
