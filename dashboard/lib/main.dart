import 'package:flutter/material.dart';
import 'package:networkagent/appstate.dart';
import 'package:provider/provider.dart';
import 'screens/login_screen.dart';

void main() async {
  runApp(
    ChangeNotifierProvider(
      create: (context) => Appstate(),
      child: MaterialApp(
        home: NetworkAgentApp()
      )
    )
  );
}

class NetworkAgentApp extends StatelessWidget {
  const NetworkAgentApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Digital Twin Dashboard',
      theme: ThemeData(
        colorScheme: ColorScheme.light(
          primary: const Color(0xFF0D47A1), // Dark blue
          secondary: const Color(0xFF1976D2), // Lighter blue
          surface: Colors.white,
          background: Colors.white,
          onPrimary: Colors.white,
        ),
        scaffoldBackgroundColor: Colors.white,
        appBarTheme: const AppBarTheme(
          backgroundColor: Color(0xFF0D47A1),
          foregroundColor: Colors.white,
        ),
        cardTheme: CardThemeData(
          color: Colors.white,
          elevation: 2,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8),
          ),
        ),
        dividerTheme: const DividerThemeData(
          color: Color(0xFFE0E0E0),
        ),
        useMaterial3: true,
      ),
      home: const LoginScreen(),
    );
  }
}
