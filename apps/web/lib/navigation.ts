import { Boxes, Gauge, Settings2 } from "lucide-react";

import type { NavigationItem } from "@/types/navigation";

export const navigationItems: NavigationItem[] = [
  { label: "客服工作台", icon: Gauge, active: true },
  { label: "能力配置", icon: Boxes },
  { label: "设置", icon: Settings2 },
];
