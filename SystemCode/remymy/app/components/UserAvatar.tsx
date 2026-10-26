import Image from "next/image";
import { GUEST_PROFILE } from "@/lib/guest-profile";

export function UserAvatar({ size }: { size: number }) {
  return <Image className="user-avatar" src={GUEST_PROFILE.avatar} alt="" width={size} height={size} unoptimized />;
}
