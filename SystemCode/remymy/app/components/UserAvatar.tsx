import Image from "next/image";
import { DEMO_PROFILE } from "@/lib/demo-profile";

export function UserAvatar({ size }: { size: number }) {
  return <Image className="user-avatar" src={DEMO_PROFILE.avatar} alt="" width={size} height={size} unoptimized />;
}
