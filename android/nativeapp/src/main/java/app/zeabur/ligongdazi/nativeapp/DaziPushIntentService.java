package app.zeabur.ligongdazi.nativeapp;

import android.content.Context;

import com.igexin.sdk.GTIntentService;
import com.igexin.sdk.message.GTCmdMessage;
import com.igexin.sdk.message.GTNotificationMessage;
import com.igexin.sdk.message.GTTransmitMessage;

/** Receives the Getui CID and notification click callbacks. */
public class DaziPushIntentService extends GTIntentService {
    @Override public void onReceiveServicePid(Context context, int pid) {
    }

    @Override public void onReceiveMessageData(Context context, GTTransmitMessage message) {
    }

    @Override public void onReceiveClientId(Context context, String clientId) {
        NativePushRegistrar.register(context, clientId);
    }

    @Override public void onReceiveOnlineState(Context context, boolean online) {
    }

    @Override public void onReceiveCommandResult(Context context, GTCmdMessage message) {
    }

    @Override public void onNotificationMessageArrived(
            Context context, GTNotificationMessage message) {
    }

    @Override public void onNotificationMessageClicked(
            Context context, GTNotificationMessage message) {
        NativePushRegistrar.openNotification(context, message.getPayload());
    }
}
