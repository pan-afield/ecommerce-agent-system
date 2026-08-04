import { Boxes, Gauge, Settings2 } from "lucide-react";

import type { NavigationItem } from "@/types/navigation";

export const navigationItems: NavigationItem[] = [
  { label: "Workspace", icon: Gauge, active: true },
  { label: "Capabilities", icon: Boxes },
  { label: "Settings", icon: Settings2 },
];
