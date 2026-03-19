// Environment configuration
class EnvironmentConfig {
  // Default values that can be overridden
  static String gcpProject = const String.fromEnvironment('GCP_PROJECT', defaultValue: 'unknown-project');
  static String giteaUrl = const String.fromEnvironment('GITEA_URL', defaultValue: 'https://gitea.example.com');
  static String agentUrl = const String.fromEnvironment('NETWORKAGENT_URL', defaultValue: 'http://127.0.0.1:9000');
  static String trainGNNUrl = const String.fromEnvironment('TRAIN_GNN_URI', defaultValue: 'http://127.0.0.1:8081');
  static String serveGNNUrl = const String.fromEnvironment('SERVE_GNN_URI', defaultValue: 'http://127.0.0.1:8082');
  static String username = const String.fromEnvironment('WEBAPPS_LOGIN', defaultValue: 'networkagent');
  static String password = const String.fromEnvironment('WEBAPPS_PWD', defaultValue: 'password123');
}
