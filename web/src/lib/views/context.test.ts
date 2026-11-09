import { expect, it, vi } from 'vitest'
import { ACTION_EVENT, BRING_EVENT, actionCompleted, bringToChat } from './context'
it('delivers typed chips and local feedback once through the window seam', () => {
 const bring = vi.fn(); const action = vi.fn()
 window.addEventListener(BRING_EVENT, bring); window.addEventListener(ACTION_EVENT, action)
 const chip = { kind:'plan' as const, ticker:'MU', id:42 }
 bringToChat(chip); actionCompleted('已记录成交')
 expect(bring).toHaveBeenCalledOnce(); expect(bring.mock.calls[0][0].detail).toEqual(chip)
 expect(action).toHaveBeenCalledOnce(); expect(action.mock.calls[0][0].detail).toEqual({ text:'已记录成交' })
 window.removeEventListener(BRING_EVENT, bring); window.removeEventListener(ACTION_EVENT, action)
})
