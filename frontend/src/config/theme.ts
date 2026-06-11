import { createLightTheme, type BrandVariants, type Theme } from "@fluentui/react-components";

const agentJBrand: BrandVariants = {
  10: "#050206",
  20: "#1B0A31",
  30: "#2C0F54",
  40: "#3C1373",
  50: "#4C1893",
  60: "#5B1FB0",
  70: "#6B27CC",
  80: "#7B3BD6",
  90: "#8B50DF",
  100: "#9A66E7",
  110: "#A87CEE",
  120: "#B692F4",
  130: "#C3A9F8",
  140: "#D0BFFB",
  150: "#DDD6FD",
  160: "#EEEDFE",
};

export const agentJTheme: Theme = {
  ...createLightTheme(agentJBrand),
};
