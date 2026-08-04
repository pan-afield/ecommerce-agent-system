import type { LucideIcon } from "lucide-react";

export interface NavigationItem {
  label: string;
  icon: LucideIcon;
  active?: boolean;
}
