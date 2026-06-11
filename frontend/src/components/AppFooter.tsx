// Stub: partner-facing footer with a placeholder privacy link and
// attribution. Replace href="#" with your real privacy policy URL and
// swap "Subject Matter Expert" for your organization name when you
// customize this template.
import { Link, Text } from "@fluentui/react-components";

interface AppFooterProps {
  className?: string;
}

export function AppFooter({ className = "app-footer" }: AppFooterProps) {
  return (
    <footer className={className}>
      <Link href="#">Privacy policy</Link>
      <Text size={200}>&nbsp;| Subject Matter Expert</Text>
    </footer>
  );
}
