import CalendarDays from '@lucide/svelte/icons/calendar-days'
import Search from '@lucide/svelte/icons/search'
import ClipboardList from '@lucide/svelte/icons/clipboard-list'
import Database from '@lucide/svelte/icons/database'
import BookOpen from '@lucide/svelte/icons/book-open'
import Wrench from '@lucide/svelte/icons/wrench'
import Settings from '@lucide/svelte/icons/settings'
import Activity from '@lucide/svelte/icons/activity'
import Bell from '@lucide/svelte/icons/bell'
import SunMoon from '@lucide/svelte/icons/sun-moon'
import X from '@lucide/svelte/icons/x'
import Check from '@lucide/svelte/icons/check'
import ChevronDown from '@lucide/svelte/icons/chevron-down'
import ChevronRight from '@lucide/svelte/icons/chevron-right'
import Info from '@lucide/svelte/icons/info'
import ExternalLink from '@lucide/svelte/icons/external-link'
import RefreshCw from '@lucide/svelte/icons/refresh-cw'
import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
import CircleX from '@lucide/svelte/icons/circle-x'
import Loader from '@lucide/svelte/icons/loader'
import Menu from '@lucide/svelte/icons/menu'
import PanelLeftClose from '@lucide/svelte/icons/panel-left-close'
import PanelLeftOpen from '@lucide/svelte/icons/panel-left-open'
import Microscope from '@lucide/svelte/icons/microscope'
import Map from '@lucide/svelte/icons/map'
import ToggleLeft from '@lucide/svelte/icons/toggle-left'

// The explicit icon table: a new icon is one import and one line here (no wildcard import, so the bundle only carries these).
export const ICONS = {
  microscope: Microscope,
  map: Map,
  'toggle-left': ToggleLeft,
  'calendar-days': CalendarDays,
  search: Search,
  'clipboard-list': ClipboardList,
  database: Database,
  'book-open': BookOpen,
  wrench: Wrench,
  settings: Settings,
  activity: Activity,
  bell: Bell,
  'sun-moon': SunMoon,
  x: X,
  check: Check,
  'chevron-down': ChevronDown,
  'chevron-right': ChevronRight,
  info: Info,
  'external-link': ExternalLink,
  'refresh-cw': RefreshCw,
  'triangle-alert': TriangleAlert,
  'circle-x': CircleX,
  loader: Loader,
  menu: Menu,
  'panel-left-close': PanelLeftClose,
  'panel-left-open': PanelLeftOpen,
} as const

export type IconName = keyof typeof ICONS
