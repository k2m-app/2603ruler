import SwiftUI
import SwiftData
import HealthKit
import Combine
import UserNotifications
import PhotosUI
import UIKit
import CoreLocation
import UniformTypeIdentifiers
import MapKit
import Charts
import NaturalLanguage

// ==========================================
// テーマカラー
// ==========================================
enum ThemeColor: Int, CaseIterable, Identifiable {
    case sageGreen = 0; case dustyRose = 1; case mutedBlue = 2; case warmSand = 3; case lavender = 4
    var id: Int { self.rawValue }
    var color: Color {
        switch self {
        case .sageGreen: return Color(red: 132/255, green: 165/255, blue: 157/255)
        case .dustyRose: return Color(red: 205/255, green: 144/255, blue: 144/255)
        case .mutedBlue: return Color(red: 138/255, green: 164/255, blue: 183/255)
        case .warmSand:  return Color(red: 212/255, green: 163/255, blue: 115/255)
        case .lavender:  return Color(red: 168/255, green: 159/255, blue: 180/255)
        }
    }
    var name: String {
        switch self {
        case .sageGreen: return "セージグリーン"; case .dustyRose: return "ダスティローズ"; case .mutedBlue: return "ミュートブルー"; case .warmSand: return "ウォームサンド"; case .lavender: return "ラベンダーグレー"
        }
    }
}

// ==========================================
// 🔔 通知マネージャー（🌟 スマート通知制御）
// ==========================================
class AppNotificationManager {
    static let shared = AppNotificationManager()
    
    func updateNotifications(logs: [JibunLog]) {
        let isEnabled = UserDefaults.standard.object(forKey: "isNotificationEnabled") as? Bool ?? true
        let hour = UserDefaults.standard.object(forKey: "notificationHour") as? Int ?? 23
        let minute = UserDefaults.standard.object(forKey: "notificationMinute") as? Int ?? 0
        
        let center = UNUserNotificationCenter.current()
        center.removeAllPendingNotificationRequests()
        
        guard isEnabled else { return }
        
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            
            let content = UNMutableNotificationContent()
            content.title = "今日もお疲れ様でした☕️"
            content.body = "1分で終わる『自分ログ』をつけて、今日を振り返りませんか？"
            content.sound = .default
            
            let calendar = Calendar.current
            let now = Date()
            let today = calendar.startOfDay(for: now)
            
            // 🌟 今日すでにログが存在するかチェック
            let hasLoggedToday = logs.contains { calendar.isDate($0.date, inSameDayAs: today) }
            
            // 向こう14日分の通知をスケジュール（すでに記録済みの日はスキップ）
            for i in 0..<14 {
                if i == 0 && hasLoggedToday { continue }
                
                guard let targetDate = calendar.date(byAdding: .day, value: i, to: today),
                      let triggerDate = calendar.date(bySettingHour: hour, minute: minute, second: 0, of: targetDate) else { continue }
                
                if triggerDate < now { continue }
                
                let components = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: triggerDate)
                let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
                let request = UNNotificationRequest(identifier: "dailyReminder_\(i)", content: content, trigger: trigger)
                
                center.add(request)
            }
        }
    }
}

// ==========================================
// 📱 アプリの土台
// ==========================================
struct ContentView: View {
    let bgColor = Color(red: 249/255, green: 248/255, blue: 244/255)
    @AppStorage("themeColorIndex") private var themeColorIndex = 0
    @AppStorage("hasCompletedOnboarding") private var hasCompletedOnboarding = false
    @State private var selectedTab = 2
    @Query private var logs: [JibunLog]
    
    var mainColor: Color { ThemeColor(rawValue: themeColorIndex)?.color ?? ThemeColor.sageGreen.color }
    
    var body: some View {
        if hasCompletedOnboarding {
            TabView(selection: $selectedTab) {
                InsightView(bgColor: bgColor, mainColor: mainColor)
                    .tabItem { Image(systemName: "lightbulb.fill"); Text("トリセツ") }.tag(0)
                NavigationStack { DiaryView(bgColor: bgColor, mainColor: mainColor) }
                    .tabItem { Image(systemName: "book.closed.fill"); Text("ダイアリー") }.tag(1)
                HomeView(bgColor: bgColor, mainColor: mainColor, selectedTab: $selectedTab)
                    .tabItem { Image(systemName: "house.circle.fill").environment(\.symbolVariants, .fill); Text("ホーム") }.tag(2)
                NavigationStack { LogView(bgColor: bgColor, mainColor: mainColor) }
                    .tabItem { Image(systemName: "calendar"); Text("ログ") }.tag(3)
                NavigationStack { SettingsView(bgColor: bgColor, mainColor: mainColor) }
                    .tabItem { Image(systemName: "gearshape.fill"); Text("設定") }.tag(4)
            }
            .tint(mainColor)
            .onAppear {
                AppNotificationManager.shared.updateNotifications(logs: logs)
            }
        } else {
            OnboardingView(mainColor: mainColor)
        }
    }
}

// ==========================================
// 👋 初回設定画面
// ==========================================
struct OnboardingView: View {
    @AppStorage("nickname") private var nickname = ""
    @AppStorage("birthday") private var birthday = Date()
    @AppStorage("postalCode") private var postalCode = ""
    @AppStorage("hasCompletedOnboarding") private var hasCompletedOnboarding = false
    let mainColor: Color
    @Query private var logs: [JibunLog]
    
    var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("あなたについて教えてください"), footer: Text("郵便番号は天気の自動取得にのみ使用されます。")) {
                    TextField("ニックネーム（必須）", text: $nickname)
                    DatePicker("誕生日", selection: $birthday, displayedComponents: .date).environment(\.locale, Locale(identifier: "ja_JP"))
                    TextField("郵便番号（ハイフンなし7桁）", text: $postalCode).keyboardType(.numberPad)
                }
                Button(action: {
                    hasCompletedOnboarding = true
                    AppNotificationManager.shared.updateNotifications(logs: logs)
                }) {
                    Text("Jibunlogをはじめる").bold().frame(maxWidth: .infinity).padding().foregroundColor(.white).background(nickname.isEmpty ? Color.gray : mainColor).cornerRadius(10)
                }.disabled(nickname.isEmpty)
            }
            .navigationTitle("初期設定")
        }
    }
}

// ==========================================
// 📦 データの設計図
// ==========================================
@Model
class JibunLog: Identifiable {
    var date: Date
    var score: Double
    var mainActivity: String
    var activityLevel: Int
    var stepCount: String
    var breakfast: String
    var lunch: String
    var dinner: String
    var foodMeat: String
    var foodFish: String
    var foodVeg: String
    var alcohol: String
    var caffeine: String
    var tobacco: String
    var exercise: String
    var sleepTime: String
    var sleepQuality: String
    var goodThing: String
    var memo: String
    var photoData: Data?
    var weather: String
    var socialInteraction: String = "-"
    
    init(date: Date, score: Double, mainActivity: String, activityLevel: Int, stepCount: String, breakfast: String, lunch: String, dinner: String, foodMeat: String, foodFish: String, foodVeg: String, alcohol: String, caffeine: String, tobacco: String, exercise: String, sleepTime: String, sleepQuality: String, goodThing: String, memo: String, photoData: Data? = nil, weather: String = "-", socialInteraction: String = "-") {
        self.date = date; self.score = score; self.mainActivity = mainActivity; self.activityLevel = activityLevel; self.stepCount = stepCount; self.breakfast = breakfast; self.lunch = lunch; self.dinner = dinner; self.foodMeat = foodMeat; self.foodFish = foodFish; self.foodVeg = foodVeg; self.alcohol = alcohol; self.caffeine = caffeine; self.tobacco = tobacco; self.exercise = exercise; self.sleepTime = sleepTime; self.sleepQuality = sleepQuality; self.goodThing = goodThing; self.memo = memo; self.photoData = photoData; self.weather = weather; self.socialInteraction = socialInteraction
    }
}

// ==========================================
// 🏃‍♂️ 歩数を取得する裏方さん
// ==========================================
class HealthKitManager: ObservableObject {
    let healthStore = HKHealthStore()
    @Published var stepCount: String = "取得中..."
    
    func requestAuthorization(for date: Date? = nil) {
        guard let stepType = HKQuantityType.quantityType(forIdentifier: .stepCount) else { return }
        healthStore.requestAuthorization(toShare: [], read: [stepType]) { success, _ in
            if success { self.fetchSteps(for: date ?? Date()) }
            else { DispatchQueue.main.async { self.stepCount = "許可が必要" } }
        }
    }
    
    func fetchSteps(for date: Date, completion: ((String) -> Void)? = nil) {
        guard let stepType = HKQuantityType.quantityType(forIdentifier: .stepCount) else { return }
        let startOfDay = Calendar.current.startOfDay(for: date)
        let endOfDay = Calendar.current.date(byAdding: .day, value: 1, to: startOfDay)!
        let predicate = HKQuery.predicateForSamples(withStart: startOfDay, end: endOfDay, options: .strictStartDate)
        
        let query = HKStatisticsQuery(quantityType: stepType, quantitySamplePredicate: predicate, options: .cumulativeSum) { _, result, _ in
            let steps = Int(result?.sumQuantity()?.doubleValue(for: HKUnit.count()) ?? 0)
            DispatchQueue.main.async {
                let stepString = "\(steps)"
                self.stepCount = stepString
                completion?(stepString)
            }
        }
        healthStore.execute(query)
    }
}

// ==========================================
// 🏠 ホーム画面
// ==========================================
struct HomeView: View {
    let bgColor: Color; let mainColor: Color; @Binding var selectedTab: Int
    @Environment(\.modelContext) private var modelContext
    @Query private var logs: [JibunLog]
    @StateObject private var healthKitManager = HealthKitManager()
    @AppStorage("nickname") private var nickname = ""
    @AppStorage("postalCode") private var postalCode = ""
    
    @State private var isFormVisible = false
    @State private var showToast = false
    
    @State private var score: Double = 5.0; @State private var mainActivity: String = "-"
    @State private var socialInteraction: String = "-"
    @State private var activityLevel: Double = 3.0; @State private var stepCount: String = ""
    @State private var breakfast: String = "-"; @State private var lunch: String = "-"; @State private var dinner: String = "-"
    @State private var foodMeat: String = "-"; @State private var foodFish: String = "-"; @State private var foodVeg: String = "-"
    @State private var alcohol: String = "-"; @State private var caffeine: String = "-"; @State private var tobacco: String = "-"; @State private var exercise: String = "-"
    @State private var sleepTime: String = "-"; @State private var sleepQuality: String = "-"
    @State private var goodThing: String = ""; @State private var memo: String = ""; @State private var weather: String = "-"
    @State private var photoData: Data? = nil; @State private var selectedPhotoItem: PhotosPickerItem? = nil

    let mainActivityOptions = ["-", "仕事", "遊び", "無"]
    let levelOptions = ["-", "無", "少", "普", "多"]
    let mealOptions = ["-", "無", "自炊", "購入", "外食"]
    let sleepTimeOptions = ["-", "-3h", "4-6h", "6-8h", "8h-"]
    let qualityOptions = ["-", "悪い", "普通", "良い"]

    var targetDate: Date {
        let now = Date()
        let hour = Calendar.current.component(.hour, from: now)
        if hour < 5 { return Calendar.current.date(byAdding: .day, value: -1, to: now) ?? now }
        return now
    }
    
    var dateTitleText: String {
        let f = DateFormatter(); f.dateFormat = "M/d"; f.locale = Locale(identifier: "ja_JP")
        return "今日(\(f.string(from: targetDate)))の記録"
    }

    var greetingText: String {
        let hour = Calendar.current.component(.hour, from: Date())
        switch hour {
        case 5..<11: return "おはようございます☀️"
        case 11..<17: return "こんにちは☕️"
        case 17..<24: return "今日もお疲れ様です🌙"
        default: return "夜遅くまでお疲れ様です🦉"
        }
    }
    
    var currentStreak: Int {
        let sortedLogs = logs.sorted { $0.date > $1.date }
        guard !sortedLogs.isEmpty else { return 0 }
        let calendar = Calendar.current; var streak = 0; var expectedDate = calendar.startOfDay(for: targetDate)
        let firstLogDate = calendar.startOfDay(for: sortedLogs[0].date)
        if (calendar.dateComponents([.day], from: firstLogDate, to: expectedDate).day ?? 0) > 1 { return 0 }
        expectedDate = firstLogDate
        for log in sortedLogs {
            let logDay = calendar.startOfDay(for: log.date)
            if logDay == expectedDate { streak += 1; expectedDate = calendar.date(byAdding: .day, value: -1, to: expectedDate)! }
            else if logDay < expectedDate { break }
        }
        return streak
    }

    @ViewBuilder func rowLabel(icon: String, text: String, color: Color = .primary) -> some View {
        HStack(spacing: 8) { Image(systemName: icon).foregroundColor(color).frame(width: 24); Text(text); Spacer() }.frame(width: 80)
    }

    var body: some View {
        ZStack {
            bgColor.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 25) {
                    VStack(spacing: 10) {
                        Text("\(nickname)さん、\n\(greetingText)").font(.title2).bold().multilineTextAlignment(.center).foregroundColor(mainColor).padding(.top, 20)
                        if currentStreak > 0 {
                            HStack { Image(systemName: "flame.fill").foregroundColor(.orange); Text("現在 \(currentStreak) 日連続で記録中！").font(.headline).foregroundColor(.secondary) }
                            .padding(.horizontal, 20).padding(.vertical, 10).background(Color.orange.opacity(0.1)).cornerRadius(20)
                        } else {
                            Text("ここから新しい記録を始めましょう！").font(.subheadline).foregroundColor(.secondary)
                        }
                    }.padding(.bottom, 10)
                    
                    Button(action: { withAnimation(.spring(response: 0.6, dampingFraction: 0.8)) { isFormVisible.toggle() } }) {
                        HStack { Image(systemName: isFormVisible ? "xmark.circle.fill" : "pencil.circle.fill"); Text(isFormVisible ? "閉じる" : "本日の記録をつける") }
                        .font(.headline).foregroundColor(.white).frame(maxWidth: .infinity).padding().background(isFormVisible ? Color.gray : mainColor).cornerRadius(15).shadow(color: (isFormVisible ? Color.gray : mainColor).opacity(0.4), radius: 8, x: 0, y: 4)
                    }.padding(.horizontal, 20)
                    
                    if isFormVisible {
                        VStack(spacing: 30) {
                            VStack {
                                HStack { Text(dateTitleText).font(.headline).foregroundColor(.white); Spacer(); Text("\(Int(score)) 点").font(.title2).bold().foregroundColor(.white) }
                                Slider(value: $score, in: 0...10, step: 1).tint(.white)
                            }.padding().background(mainColor).cornerRadius(15).shadow(color: mainColor.opacity(0.4), radius: 8, x: 0, y: 4)
                            
                            VStack(alignment: .leading, spacing: 15) {
                                HStack { Image(systemName: "figure.walk"); Text("今日の過ごし方").font(.headline) }
                                Picker("主にしたこと", selection: $mainActivity) { ForEach(mainActivityOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented)
                                if mainActivity == "仕事" || mainActivity == "遊び" { Text("充実度・忙しさ: \(Int(activityLevel))").font(.subheadline); Slider(value: $activityLevel, in: 1...5, step: 1).tint(mainColor) }
                                Divider()
                                HStack { rowLabel(icon: "person.2.fill", text: "人との関わり", color: .purple); Spacer(); Picker("", selection: $socialInteraction) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                            }.padding().background(Color.white).cornerRadius(15)

                            VStack(alignment: .leading, spacing: 15) {
                                HStack { Image(systemName: "leaf.fill"); Text("天気と歩数").font(.headline) }
                                HStack { rowLabel(icon: "cloud.fill", text: "天気", color: .cyan); Spacer(); Picker("", selection: $weather) { Text("-").tag("-"); Image(systemName: "sun.max.fill").tag("☀️"); Image(systemName: "cloud.fill").tag("☁️"); Image(systemName: "cloud.rain.fill").tag("☔️"); Image(systemName: "snowflake").tag("☃️") }.pickerStyle(.segmented) }
                                HStack { rowLabel(icon: "shoeprints.fill", text: "歩数"); Spacer(); TextField("歩数", text: $healthKitManager.stepCount).keyboardType(.numberPad).textFieldStyle(RoundedBorderTextFieldStyle()).frame(width: 100); Text("歩") }
                            }.padding().background(Color.white).cornerRadius(15)
                            
                            VStack(alignment: .leading, spacing: 15) {
                                HStack { Image(systemName: "fork.knife"); Text("食事").font(.headline) }
                                HStack { rowLabel(icon: "sun.max.fill", text: "朝", color: .orange); Spacer(); Picker("", selection: $breakfast) { ForEach(mealOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                                HStack { rowLabel(icon: "sun.haze.fill", text: "昼", color: .orange); Spacer(); Picker("", selection: $lunch) { ForEach(mealOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                                HStack { rowLabel(icon: "moon.stars.fill", text: "夜", color: .blue); Spacer(); Picker("", selection: $dinner) { ForEach(mealOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                                Divider()
                                HStack { rowLabel(icon: "frying.pan.fill", text: "肉"); Spacer(); Picker("", selection: $foodMeat) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                                HStack { rowLabel(icon: "fish.fill", text: "魚", color: .blue); Spacer(); Picker("", selection: $foodFish) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                                HStack { rowLabel(icon: "carrot.fill", text: "野菜", color: .orange); Spacer(); Picker("", selection: $foodVeg) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                            }.padding().background(Color.white).cornerRadius(15)
                            
                            VStack(alignment: .leading, spacing: 15) {
                                HStack { Image(systemName: "figure.run"); Text("習慣・アクティビティ").font(.headline) }
                                HStack { rowLabel(icon: "wineglass.fill", text: "酒", color: .purple); Spacer(); Picker("", selection: $alcohol) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                                HStack { rowLabel(icon: "mug.fill", text: "カフェイン", color: .brown); Spacer(); Picker("", selection: $caffeine) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                                HStack { rowLabel(icon: "smoke.fill", text: "タバコ", color: .gray); Spacer(); Picker("", selection: $tobacco) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                                HStack { rowLabel(icon: "dumbbell.fill", text: "運動", color: .green); Spacer(); Picker("", selection: $exercise) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                            }.padding().background(Color.white).cornerRadius(15)
                            
                            VStack(alignment: .leading, spacing: 15) {
                                HStack { Image(systemName: "bed.double.fill"); Text("睡眠").font(.headline) }
                                HStack { rowLabel(icon: "clock.fill", text: "時間"); Spacer(); Picker("", selection: $sleepTime) { ForEach(sleepTimeOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                                HStack { rowLabel(icon: "star.fill", text: "質", color: .yellow); Spacer(); Picker("", selection: $sleepQuality) { ForEach(qualityOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }
                            }.padding().background(Color.white).cornerRadius(15)
                            
                            VStack(alignment: .leading, spacing: 15) {
                                HStack { Image(systemName: "square.and.pencil"); Text("今日の振り返り").font(.headline) }
                                TextField("今日良かったこと（30文字以内）", text: $goodThing).textFieldStyle(RoundedBorderTextFieldStyle()).onChange(of: goodThing) { _, newValue in if newValue.count > 30 { goodThing = String(newValue.prefix(30)) } }
                                TextEditor(text: $memo).frame(height: 100).overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.gray.opacity(0.3), lineWidth: 1))
                                PhotosPicker(selection: $selectedPhotoItem, matching: .images) {
                                    HStack { Image(systemName: "photo.on.rectangle.angled"); Text(photoData == nil ? "今日の1枚を追加" : "写真を変更する") }.foregroundColor(mainColor).frame(maxWidth: .infinity).padding().background(mainColor.opacity(0.1)).cornerRadius(10)
                                }
                                .onChange(of: selectedPhotoItem) { _, newValue in Task { if let data = try? await newValue?.loadTransferable(type: Data.self) { photoData = data } } }
                                if let photoData, let uiImage = UIImage(data: photoData) { Image(uiImage: uiImage).resizable().scaledToFill().frame(height: 200).frame(maxWidth: .infinity).clipped().cornerRadius(10) }
                            }.padding().background(Color.white).cornerRadius(15)
                            
                            Button(action: {
                                let saveDate = targetDate
                                if let existingLog = logs.first(where: { Calendar.current.isDate($0.date, inSameDayAs: saveDate) }) { modelContext.delete(existingLog) }
                                let newLog = JibunLog(date: saveDate, score: score, mainActivity: mainActivity, activityLevel: Int(activityLevel), stepCount: healthKitManager.stepCount, breakfast: breakfast, lunch: lunch, dinner: dinner, foodMeat: foodMeat, foodFish: foodFish, foodVeg: foodVeg, alcohol: alcohol, caffeine: caffeine, tobacco: tobacco, exercise: exercise, sleepTime: sleepTime, sleepQuality: sleepQuality, goodThing: goodThing, memo: memo, photoData: photoData, weather: weather, socialInteraction: socialInteraction)
                                modelContext.insert(newLog)
                                
                                AppNotificationManager.shared.updateNotifications(logs: logs)
                                
                                withAnimation(.spring()) { showToast = true; isFormVisible = false }
                                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                                    withAnimation { showToast = false }
                                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { selectedTab = 0 }
                                }
                            }) { Text("記録を保存する").font(.headline).foregroundColor(.white).frame(maxWidth: .infinity).padding(.vertical, 16).background(mainColor).cornerRadius(15) }.padding(.bottom, 60)
                        }.padding(.horizontal, 20).transition(.move(edge: .bottom).combined(with: .opacity))
                    } else { Spacer().frame(height: 100) }
                }
            }
            if showToast { VStack { Spacer(); Text("記録完了！今日も１日お疲れさまでした！").font(.subheadline).bold().foregroundColor(.white).padding(.horizontal, 24).padding(.vertical, 14).background(mainColor.opacity(0.95)).cornerRadius(25).shadow(radius: 10).padding(.bottom, 20) }.transition(.move(edge: .bottom).combined(with: .opacity)).zIndex(1) }
        }
        .onAppear { healthKitManager.requestAuthorization(for: targetDate); fetchWeather() }
    }
    private func fetchWeather() {}
}

// ==========================================
// 💡 トリセツ画面（🌟 美しいUI＆チャート切り替え）
// ==========================================
struct InsightItem: Identifiable { let id = UUID(); let title: String; let icon: String; let description: String; let color: Color }

struct ImpactFactor: Identifiable {
    let id = UUID()
    let title: String
    let value: String
    let impact: Double
    let icon: String
    let color: Color
}

struct InsightView: View {
    let bgColor: Color; let mainColor: Color
    @Query(sort: \JibunLog.date, order: .reverse) private var logs: [JibunLog]
    
    // 🌟 平均スコアの計算
    var averageScore: Double { if logs.isEmpty { return 0 }; return logs.reduce(0) { $0 + $1.score } / Double(logs.count) }
    
    var weekAverageScore: Double {
        guard let oneWeekAgo = Calendar.current.date(byAdding: .day, value: -7, to: Date()) else { return 0 }
        let filtered = logs.filter { $0.date >= oneWeekAgo }
        if filtered.isEmpty { return 0 }
        return filtered.reduce(0) { $0 + $1.score } / Double(filtered.count)
    }
    
    var monthAverageScore: Double {
        guard let oneMonthAgo = Calendar.current.date(byAdding: .month, value: -1, to: Date()) else { return 0 }
        let filtered = logs.filter { $0.date >= oneMonthAgo }
        if filtered.isEmpty { return 0 }
        return filtered.reduce(0) { $0 + $1.score } / Double(filtered.count)
    }

    var recentLogs: [JibunLog] { Array(logs.prefix(14)) }
    
    var happyKeywords: [String] {
        let texts = logs.filter { $0.score >= averageScore }.map { $0.goodThing }.joined(separator: " ")
        guard !texts.isEmpty else { return [] }
        let tokenizer = NLTokenizer(unit: .word); tokenizer.string = texts; var wordCounts: [String: Int] = [:]
        tokenizer.enumerateTokens(in: texts.startIndex..<texts.endIndex) { range, _ in
            let word = String(texts[range]); if word.count > 1 { wordCounts[word, default: 0] += 1 }; return true
        }
        return wordCounts.sorted { $0.value > $1.value }.prefix(4).map { $0.key }
    }
    
    var impactFactors: [ImpactFactor] {
        guard logs.count >= 3 else { return [] }
        let overallAvg = averageScore
        var factors: [String: (total: Double, count: Int, title: String, value: String, icon: String, color: Color)] = [:]
        
        func add(_ val: String, title: String, icon: String, color: Color, score: Double) {
            if val != "-" && val != "無" && val != "" {
                let key = "\(title):\(val)"
                if factors[key] == nil { factors[key] = (0, 0, title, val, icon, color) }
                factors[key]!.total += score
                factors[key]!.count += 1
            }
        }
        
        for log in logs {
            add(log.sleepQuality, title: "睡眠の質", icon: "star.fill", color: .yellow, score: log.score)
            add(log.sleepTime, title: "睡眠時間", icon: "clock.fill", color: .blue, score: log.score)
            add(log.exercise, title: "運動", icon: "figure.run", color: .green, score: log.score)
            add(log.alcohol, title: "酒", icon: "wineglass.fill", color: .purple, score: log.score)
            add(log.breakfast, title: "朝食", icon: "sun.max.fill", color: .orange, score: log.score)
            add(log.foodVeg, title: "野菜", icon: "carrot.fill", color: .green, score: log.score)
            add(log.socialInteraction, title: "人との関わり", icon: "person.2.fill", color: .purple, score: log.score)
            add(log.mainActivity, title: "行動", icon: "figure.walk", color: .blue, score: log.score)
        }
        
        var result: [ImpactFactor] = []
        for (_, data) in factors {
            if data.count >= 2 {
                let avg = data.total / Double(data.count)
                let impact = avg - overallAvg
                if abs(impact) >= 0.3 { result.append(ImpactFactor(title: data.title, value: data.value, impact: impact, icon: data.icon, color: data.color)) }
            }
        }
        
        let sorted = result.sorted { $0.impact > $1.impact }
        let top = Array(sorted.prefix(3))
        let bottom = Array(sorted.suffix(3).filter { $0.impact < 0 })
        var final = top
        for b in bottom { if !final.contains(where: { $0.title == b.title && $0.value == b.value }) { final.append(b) } }
        return final.sorted { $0.impact > $1.impact }
    }
    
    var dynamicInsights: [InsightItem] {
        if logs.count < 3 { return [] }
        var insights: [InsightItem] = []
        let sortedLogs = logs.sorted { $0.score > $1.score }
        let halfIndex = max(1, sortedLogs.count / 2)
        let topLogs = Array(sortedLogs.prefix(halfIndex))
        let bottomLogs = Array(sortedLogs.suffix(sortedLogs.count - halfIndex))
        
        func analyze(title: String, icon: String, color: Color, description: String, condition: (JibunLog) -> Bool) {
            let topRatio = Double(topLogs.filter(condition).count) / Double(max(1, topLogs.count))
            let bottomRatio = bottomLogs.isEmpty ? 0 : Double(bottomLogs.filter(condition).count) / Double(max(1, bottomLogs.count))
            if topRatio >= 0.5 && topRatio > bottomRatio { insights.append(InsightItem(title: title, icon: icon, description: description, color: color)) }
        }
        
        analyze(title: "良質な睡眠の恩恵", icon: "bed.double.fill", color: .blue, description: "点数に良い影響をもたらす最強の回復魔法です。", condition: { ($0.sleepQuality == "良い" || $0.sleepTime == "8h-" || $0.sleepTime == "6-8h") && $0.sleepQuality != "-" })
        analyze(title: "朝食のパワー", icon: "sun.max.fill", color: .orange, description: "1日のパフォーマンスや充実度が底上げされています。", condition: { $0.breakfast != "-" && $0.breakfast != "無" })
        analyze(title: "クリアな心身", icon: "drop.fill", color: .cyan, description: "お酒を控えた日ほど、コンディションが安定しています。", condition: { $0.alcohol == "無" })
        insights.append(InsightItem(title: "自分と向き合う才能", icon: "sparkles", description: "調子が良い日も悪い日も記録を残せていること自体が素晴らしい才能です！", color: .orange))
        
        return Array(insights.prefix(4))
    }

    var body: some View {
        ZStack {
            bgColor.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 25) {
                    Text("わたしのとりあつかい説明書").font(.title2).bold().padding(.horizontal, 20).padding(.bottom, -10)
                    
                    if logs.count < 3 {
                        Text("あと \(3 - logs.count) 日分記録すると、AIがあなたの傾向を徹底分析します。").foregroundColor(.gray).padding(.horizontal, 20)
                    } else {
                        // ① 🌟 概要カード（横並びで綺麗に）
                        VStack(spacing: 15) {
                            HStack {
                                VStack { Text("記録日数").font(.caption).foregroundColor(.gray); Text("\(logs.count) 日").font(.title3).bold().foregroundColor(mainColor) }.frame(maxWidth: .infinity)
                                Divider()
                                VStack { Text("通算平均").font(.caption).foregroundColor(.gray); Text(String(format: "%.1f 点", averageScore)).font(.title3).bold().foregroundColor(.orange) }.frame(maxWidth: .infinity)
                            }
                            Divider()
                            HStack {
                                VStack { Text("直近1ヶ月").font(.caption).foregroundColor(.gray); Text(String(format: "%.1f 点", monthAverageScore)).font(.headline).bold().foregroundColor(.orange) }.frame(maxWidth: .infinity)
                                Divider()
                                VStack { Text("直近1週間").font(.caption).foregroundColor(.gray); Text(String(format: "%.1f 点", weekAverageScore)).font(.headline).bold().foregroundColor(.orange) }.frame(maxWidth: .infinity)
                            }
                        }
                        .padding().background(Color.white).cornerRadius(15).shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
                        .padding(.horizontal, 20)
                        
                        // ② 🌟 チャート（スワイプ切り替え）
                        TabView {
                            // タブA：最近のスコア推移
                            VStack(alignment: .leading, spacing: 10) {
                                HStack { Image(systemName: "chart.xyaxis.line").foregroundColor(mainColor); Text("最近のスコア推移").font(.headline) }
                                Text("直近14日間の気分の波を表しています。").font(.caption).foregroundColor(.gray)
                                Chart {
                                    ForEach(recentLogs.sorted(by: { $0.date < $1.date })) { log in
                                        LineMark(x: .value("Date", log.date, unit: .day), y: .value("Score", log.score)).symbol(Circle()).foregroundStyle(mainColor)
                                        AreaMark(x: .value("Date", log.date, unit: .day), y: .value("Score", log.score)).foregroundStyle(LinearGradient(gradient: Gradient(colors: [mainColor.opacity(0.3), Color.clear]), startPoint: .top, endPoint: .bottom))
                                    }
                                }.chartYScale(domain: 0...10).frame(height: 180)
                                Spacer(minLength: 0)
                            }
                            .padding().background(Color.white).cornerRadius(15).shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
                            .padding(.horizontal, 20).padding(.bottom, 45) // インジケーターのための余白
                            
                            // タブB：スコア影響度
                            if !impactFactors.isEmpty {
                                VStack(alignment: .leading, spacing: 10) {
                                    HStack { Image(systemName: "chart.bar.fill").foregroundColor(mainColor); Text("スコアへの影響要素").font(.headline) }
                                    Text("行動が全体平均スコアにどう影響したかを示しています。").font(.caption).foregroundColor(.gray)
                                    Chart {
                                        ForEach(impactFactors) { factor in
                                            BarMark(x: .value("Impact", factor.impact), y: .value("Factor", "\(factor.title): \(factor.value)"))
                                                .foregroundStyle(factor.impact > 0 ? Color.green.opacity(0.8) : Color.red.opacity(0.8))
                                                .annotation(position: factor.impact > 0 ? .trailing : .leading) {
                                                    Text(String(format: "%+.1f", factor.impact)).font(.caption2).bold().foregroundColor(factor.impact > 0 ? .green : .red)
                                                }
                                        }
                                    }.chartXAxis { AxisMarks(values: .automatic) { value in AxisGridLine(); AxisValueLabel() } }.frame(height: 180)
                                    Spacer(minLength: 0)
                                }
                                .padding().background(Color.white).cornerRadius(15).shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
                                .padding(.horizontal, 20).padding(.bottom, 45)
                            }
                        }
                        .tabViewStyle(PageTabViewStyle(indexDisplayMode: .always))
                        .frame(height: 350)
                        
                        // ③ ハッピーキーワード
                        if !happyKeywords.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                HStack { Image(systemName: "quote.bubble.fill").foregroundColor(.orange); Text("ハッピー・キーワード").font(.headline) }
                                Text("調子が良い時によく登場する言葉です。").font(.caption).foregroundColor(.gray)
                                HStack { ForEach(happyKeywords, id: \.self) { word in Text(word).font(.subheadline).bold().padding(.horizontal, 12).padding(.vertical, 6).background(Color.orange.opacity(0.2)).foregroundColor(.orange).cornerRadius(15) } }
                            }.padding().background(Color.white).cornerRadius(15).shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
                            .padding(.horizontal, 20)
                        }
                        
                        // ④ AIからのアドバイス
                        VStack(alignment: .leading, spacing: 10) {
                            Text("💡 AIからのアドバイス").font(.headline).padding(.top, 10).padding(.bottom, 5)
                            ForEach(dynamicInsights) { insight in
                                AnalysisCard(title: insight.title, icon: insight.icon, description: insight.description, color: insight.color)
                            }
                        }.padding(.horizontal, 20)
                    }
                }
                .padding(.vertical, 20)
            }
        }
        .onAppear {
            // スクロールインジケーターの色をテーマカラーに合わせる
            UIPageControl.appearance().currentPageIndicatorTintColor = UIColor(mainColor)
            UIPageControl.appearance().pageIndicatorTintColor = UIColor(mainColor).withAlphaComponent(0.2)
        }
    }
}

struct AnalysisCard: View {
    let title: String; let icon: String; let description: String; let color: Color
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Image(systemName: icon).foregroundColor(color); Text(title).font(.headline) }
            Text(description).font(.subheadline).foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding().background(Color.white).cornerRadius(15).shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
    }
}

// ==========================================
// 📅 ログ画面
// ==========================================
struct LogView: View {
    let bgColor: Color; let mainColor: Color
    @Query private var logs: [JibunLog]; @State private var selectedDate = Date()
    
    var body: some View {
        ZStack {
            bgColor.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 20) {
                    CustomCalendarView(selectedDate: $selectedDate, logs: logs, mainColor: mainColor)
                        .padding().background(Color.white).cornerRadius(15).padding(.horizontal).padding(.top, 20)
                    
                    if let log = logs.first(where: { Calendar.current.isDate($0.date, inSameDayAs: selectedDate) }) {
                        NavigationLink(destination: LogDetailView(log: log, bgColor: bgColor, mainColor: mainColor)) {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text("この日の記録を見る").font(.caption).foregroundColor(.gray)
                                    Text("\(Int(log.score))点").font(.title).bold().foregroundColor(mainColor)
                                }
                                Spacer()
                                Text(log.weather).font(.title2)
                                Text(log.mainActivity).font(.headline).foregroundColor(.primary)
                                Image(systemName: "chevron.right").foregroundColor(.gray)
                            }.padding().background(Color.white).cornerRadius(15).padding(.horizontal).shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
                        }
                    } else {
                        VStack(spacing: 15) {
                            Text("この日の記録はありません").foregroundColor(.gray)
                            NavigationLink(destination: AddPastLogView(selectedDate: selectedDate, bgColor: bgColor, mainColor: mainColor)) {
                                Text("記録を追加する").font(.headline).foregroundColor(.white).padding(.vertical, 12).padding(.horizontal, 30).background(mainColor).cornerRadius(25)
                            }
                        }.padding()
                    }
                    Spacer().frame(height: 40)
                }
            }
        }
        .navigationTitle("記録ログ")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// ==========================================
// 📖 ダイアリー画面
// ==========================================
enum SortType { case dateDesc, dateAsc, scoreDesc, scoreAsc }

struct DiaryFilter {
    var sortType: SortType = .dateDesc
    var searchText = ""; var minScore: Double = 0.0
    var weather = "すべて"; var activity = "すべて"; var social = "すべて"
    var breakfast = "すべて"; var lunch = "すべて"; var dinner = "すべて"
    var meat = "すべて"; var fish = "すべて"; var veg = "すべて"
    var alcohol = "すべて"; var caffeine = "すべて"; var tobacco = "すべて"; var exercise = "すべて"
    var sleepTime = "すべて"; var sleepQuality = "すべて"
}

struct DiaryView: View {
    let bgColor: Color; let mainColor: Color
    @Query private var logs: [JibunLog]
    @State private var showingFilter = false
    @State private var filter = DiaryFilter()
    
    var filteredAndSortedLogs: [JibunLog] {
        var result = logs
        if !filter.searchText.isEmpty { let text = filter.searchText.lowercased(); result = result.filter { $0.memo.lowercased().contains(text) || $0.goodThing.lowercased().contains(text) || $0.mainActivity.lowercased().contains(text) || $0.weather.contains(text) } }
        if filter.minScore > 0 { result = result.filter { $0.score >= filter.minScore } }
        if filter.weather != "すべて" { result = result.filter { $0.weather == filter.weather } }
        if filter.activity != "すべて" { result = result.filter { $0.mainActivity == filter.activity } }
        if filter.social != "すべて" { result = result.filter { $0.socialInteraction == filter.social } }
        if filter.breakfast != "すべて" { result = result.filter { $0.breakfast == filter.breakfast } }
        if filter.lunch != "すべて" { result = result.filter { $0.lunch == filter.lunch } }
        if filter.dinner != "すべて" { result = result.filter { $0.dinner == filter.dinner } }
        if filter.meat != "すべて" { result = result.filter { $0.foodMeat == filter.meat } }
        if filter.fish != "すべて" { result = result.filter { $0.foodFish == filter.fish } }
        if filter.veg != "すべて" { result = result.filter { $0.foodVeg == filter.veg } }
        if filter.alcohol != "すべて" { result = result.filter { $0.alcohol == filter.alcohol } }
        if filter.caffeine != "すべて" { result = result.filter { $0.caffeine == filter.caffeine } }
        if filter.tobacco != "すべて" { result = result.filter { $0.tobacco == filter.tobacco } }
        if filter.exercise != "すべて" { result = result.filter { $0.exercise == filter.exercise } }
        if filter.sleepTime != "すべて" { result = result.filter { $0.sleepTime == filter.sleepTime } }
        if filter.sleepQuality != "すべて" { result = result.filter { $0.sleepQuality == filter.sleepQuality } }
        
        switch filter.sortType { case .dateDesc: result.sort { $0.date > $1.date }; case .dateAsc: result.sort { $0.date < $1.date }; case .scoreDesc: result.sort { $0.score > $1.score }; case .scoreAsc: result.sort { $0.score < $1.score } }
        return result
    }

    var body: some View {
        ZStack {
            bgColor.ignoresSafeArea()
            if logs.isEmpty {
                VStack { Image(systemName: "book.closed").font(.system(size: 50)).foregroundColor(.gray).padding(); Text("まだ日記がありません").foregroundColor(.gray); Text("ホームやログから記録をつけると、\nここにAIが書いたような日記が生成されます。").font(.caption).foregroundColor(.gray).multilineTextAlignment(.center).padding(.top, 5) }
            } else {
                ScrollView {
                    VStack(spacing: 20) {
                        if filteredAndSortedLogs.isEmpty { VStack(spacing: 15) { Image(systemName: "magnifyingglass").font(.system(size: 40)).foregroundColor(.gray); Text("条件に一致する記録がありません").foregroundColor(.gray) }.padding(.top, 60) }
                        else { ForEach(filteredAndSortedLogs) { log in DiaryEntryCard(log: log, mainColor: mainColor) } }
                    }.padding(20)
                }
            }
        }
        .navigationTitle("ダイアリー")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button(action: { showingFilter = true }) { Image(systemName: "magnifyingglass").font(.system(size: 18, weight: .bold)).foregroundColor(mainColor) } } }
        .sheet(isPresented: $showingFilter) { FilterSortSheet(filter: $filter, mainColor: mainColor).presentationDetents([.large]) }
    }
}

// 🌟 ダイアリーテキスト生成の進化版
struct DiaryEntryCard: View {
    var log: JibunLog; var mainColor: Color
    var dateTitle: String { let f = DateFormatter(); f.dateFormat = "yyyy年M月d日(E)"; f.locale = Locale(identifier: "ja_JP"); return f.string(from: log.date) }
    
    var generatedText: String {
        var text = ""
        
        // 絵文字と食事の日本語変換ヘルパー
        func wStr(_ e: String) -> String { switch e { case "☀️": return "晴れ"; case "☁️": return "曇り"; case "☔️": return "雨"; case "☃️": return "雪"; default: return e } }
        func mStr(_ v: String) -> String { switch v { case "自炊": return "自炊"; case "購入": return "購入したもの"; case "外食": return "外食"; default: return v } }
        
        // 1. 天気 -> したこと -> 歩数
        let activityStr = (log.mainActivity != "-" && log.mainActivity != "無") ? "主に「\(log.mainActivity)」をして過ごし" : "特に大きなイベントもなく過ごし"
        let stepStr = (log.stepCount != "" && log.stepCount != "0" && log.stepCount != "取得中...") ? "、歩数は\(log.stepCount)歩だった。\n" : "た。\n"
        
        if log.weather == "-" {
            text += "今日は\(activityStr)\(stepStr)"
        } else {
            text += "今日は\(wStr(log.weather))だった。\(activityStr)\(stepStr)"
        }
        
        // 2. 人との関わり
        switch log.socialInteraction {
        case "多": text += "人との関わりも多かった。\n"
        case "普": text += "人との関わりもほどほどにあった。\n"
        case "少": text += "人との関わりは少なめだった。\n"
        case "無": text += "誰とも関わらず、一人で静かに過ごした。\n"
        default: break
        }
        
        // 3. 食事
        var meals = [String]()
        if log.breakfast != "-" && log.breakfast != "無" { meals.append("朝は\(mStr(log.breakfast))") }
        if log.lunch != "-" && log.lunch != "無" { meals.append("昼は\(mStr(log.lunch))") }
        if log.dinner != "-" && log.dinner != "無" { meals.append("夜は\(mStr(log.dinner))") }
        
        if !meals.isEmpty {
            if meals.count == 3 {
                text += "食事は朝昼夜しっかり食べて、\(meals.joined(separator: "、"))だった。\n"
            } else {
                text += "食事は\(meals.joined(separator: "、"))だった。\n"
            }
        } else if log.breakfast == "無" && log.lunch == "無" && log.dinner == "無" {
            text += "今日は食事をとらなかった。\n"
        }
        
        // 4. アクティビティ (酒、運動、睡眠など)
        var habits = [String]()
        if log.alcohol == "多" { habits.append("お酒をしっかり飲んだ") }
        else if log.alcohol == "普" { habits.append("お酒もほどほどに飲んだ") }
        else if log.alcohol == "少" { habits.append("お酒を少し飲んだ") }
        else if log.alcohol == "無" { habits.append("お酒は飲まなかった") }
        
        if log.exercise == "多" { habits.append("運動もしっかりできた") }
        else if log.exercise == "普" { habits.append("適度に運動もできた") }
        else if log.exercise == "少" { habits.append("少し体を動かした") }
        else if log.exercise == "無" { habits.append("運動はしなかった") }

        if log.sleepQuality == "良い" || log.sleepTime == "8h-" { habits.append("睡眠もばっちりだ") }
        else if log.sleepQuality == "悪い" || log.sleepTime == "-3h" { habits.append("少し寝不足気味だ") }
        
        if !habits.isEmpty {
            if habits.count >= 2 {
                let last = habits.removeLast()
                text += habits.joined(separator: "し、") + "し、" + last + "。\n"
            } else {
                text += habits[0] + "。\n"
            }
        }
        
        // 5. ハイライト、メモ (🌟 ご要望のフォーマットに変更)
        if !log.goodThing.isEmpty {
            text += "\n✨良かったこと\n\(log.goodThing)\n"
        }
        if !log.memo.isEmpty {
            text += "\n📝メモ\n\(log.memo)\n"
        }
        
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Text(dateTitle).font(.headline).foregroundColor(mainColor); Spacer(); Text("\(Int(log.score)) 点").font(.title3).bold().foregroundColor(mainColor) }
            Divider()
            Text(generatedText).font(.body).lineSpacing(5).foregroundColor(.primary)
            if let photoData = log.photoData, let uiImage = UIImage(data: photoData) { Image(uiImage: uiImage).resizable().scaledToFill().frame(height: 150).frame(maxWidth: .infinity).clipped().cornerRadius(10).padding(.top, 5) }
        }.padding().background(Color.white).cornerRadius(15).shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)
    }
}

struct FilterSortSheet: View {
    @Environment(\.dismiss) var dismiss; @Binding var filter: DiaryFilter; let mainColor: Color
    let activities = ["すべて", "-", "仕事", "遊び", "無"]; let weathers = ["すべて", "-", "☀️", "☁️", "☔️", "☃️"]
    let levels = ["すべて", "-", "無", "少", "普", "多"]; let meals = ["すべて", "-", "無", "自炊", "購入", "外食"]
    let sleepTimes = ["すべて", "-", "-3h", "4-6h", "6-8h", "8h-"]; let qualities = ["すべて", "-", "悪い", "普通", "良い"]
    
    var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("検索・並び替え")) {
                    Picker("並び順", selection: $filter.sortType) { Text("日付 (新しい順)").tag(SortType.dateDesc); Text("日付 (古い順)").tag(SortType.dateAsc); Text("点数 (高い順)").tag(SortType.scoreDesc); Text("点数 (低い順)").tag(SortType.scoreAsc) }
                    TextField("キーワード検索", text: $filter.searchText)
                    HStack { Text("最低点数: \(Int(filter.minScore))点"); Spacer(); Slider(value: $filter.minScore, in: 0...10, step: 1).frame(width: 120).tint(mainColor) }
                }
                Section(header: Text("行動・環境")) {
                    Picker("天気", selection: $filter.weather) { ForEach(weathers, id: \.self) { Text($0) } }
                    Picker("主にしたこと", selection: $filter.activity) { ForEach(activities, id: \.self) { Text($0) } }
                    Picker("人との関わり", selection: $filter.social) { ForEach(levels, id: \.self) { Text($0) } }
                }
                Section(header: Text("食事")) {
                    Picker("朝食", selection: $filter.breakfast) { ForEach(meals, id: \.self) { Text($0) } }
                    Picker("昼食", selection: $filter.lunch) { ForEach(meals, id: \.self) { Text($0) } }
                    Picker("夕食", selection: $filter.dinner) { ForEach(meals, id: \.self) { Text($0) } }
                    Picker("肉", selection: $filter.meat) { ForEach(levels, id: \.self) { Text($0) } }
                    Picker("魚", selection: $filter.fish) { ForEach(levels, id: \.self) { Text($0) } }
                    Picker("野菜", selection: $filter.veg) { ForEach(levels, id: \.self) { Text($0) } }
                }
                Section(header: Text("習慣・睡眠")) {
                    Picker("酒", selection: $filter.alcohol) { ForEach(levels, id: \.self) { Text($0) } }
                    Picker("カフェイン", selection: $filter.caffeine) { ForEach(levels, id: \.self) { Text($0) } }
                    Picker("タバコ", selection: $filter.tobacco) { ForEach(levels, id: \.self) { Text($0) } }
                    Picker("運動", selection: $filter.exercise) { ForEach(levels, id: \.self) { Text($0) } }
                    Picker("睡眠時間", selection: $filter.sleepTime) { ForEach(sleepTimes, id: \.self) { Text($0) } }
                    Picker("睡眠の質", selection: $filter.sleepQuality) { ForEach(qualities, id: \.self) { Text($0) } }
                }
                Section { Button(action: { filter = DiaryFilter() }) { Text("すべての条件をリセット").foregroundColor(.red).frame(maxWidth: .infinity, alignment: .center) } }
            }.navigationTitle("絞り込み").navigationBarTitleDisplayMode(.inline).toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("完了") { dismiss() }.bold().foregroundColor(mainColor) } }
        }
    }
}

// ==========================================
// 🔍 記録の詳細画面
// ==========================================
struct LogDetailView: View {
    var log: JibunLog; let bgColor: Color; let mainColor: Color
    var dateTitle: String { let f = DateFormatter(); f.dateFormat = "yyyy/M/d(E)の記録"; f.locale = Locale(identifier: "ja_JP"); return f.string(from: log.date) }
    @ViewBuilder func readOnlyRow(icon: String, text: String, value: String, color: Color = .primary) -> some View { HStack(spacing: 8) { Image(systemName: icon).foregroundColor(color).frame(width: 24); Text(text); Spacer(); Text(value).bold().foregroundColor(.secondary) }.padding(.vertical, 2) }

    var body: some View {
        ZStack {
            bgColor.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 20) {
                    VStack { Text("この日の点数").font(.headline).foregroundColor(mainColor); Text("\(Int(log.score)) 点").font(.system(size: 45, weight: .bold)).foregroundColor(mainColor) }.frame(maxWidth: .infinity).padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { readOnlyRow(icon: "checkmark.circle.fill", text: "主にしたこと", value: log.mainActivity); readOnlyRow(icon: "person.2.fill", text: "人との関わり", value: log.socialInteraction, color: .purple); readOnlyRow(icon: "cloud.fill", text: "天気", value: log.weather, color: .cyan); readOnlyRow(icon: "shoeprints.fill", text: "歩数", value: "\(log.stepCount) 歩") }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "fork.knife"); Text("食事").font(.headline) }; readOnlyRow(icon: "sun.max.fill", text: "朝", value: log.breakfast, color: .orange); readOnlyRow(icon: "sun.haze.fill", text: "昼", value: log.lunch, color: .orange); readOnlyRow(icon: "moon.stars.fill", text: "夜", value: log.dinner, color: .blue); Divider(); readOnlyRow(icon: "frying.pan.fill", text: "肉", value: log.foodMeat); readOnlyRow(icon: "fish.fill", text: "魚", value: log.foodFish, color: .blue); readOnlyRow(icon: "carrot.fill", text: "野菜", value: log.foodVeg, color: .orange) }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "figure.run"); Text("習慣").font(.headline) }; readOnlyRow(icon: "wineglass.fill", text: "酒", value: log.alcohol, color: .purple); readOnlyRow(icon: "mug.fill", text: "カフェイン", value: log.caffeine, color: .brown); readOnlyRow(icon: "smoke.fill", text: "タバコ", value: log.tobacco, color: .gray); readOnlyRow(icon: "dumbbell.fill", text: "運動", value: log.exercise, color: .green) }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "bed.double.fill"); Text("睡眠").font(.headline) }; readOnlyRow(icon: "clock.fill", text: "時間", value: log.sleepTime); readOnlyRow(icon: "star.fill", text: "質", value: log.sleepQuality, color: .yellow) }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "square.and.pencil"); Text("振り返り").font(.headline) }; if !log.goodThing.isEmpty { VStack(alignment: .leading) { Text("よかったこと").font(.caption).foregroundColor(.gray); Text(log.goodThing) } }; if !log.memo.isEmpty { VStack(alignment: .leading) { Text("メモ").font(.caption).foregroundColor(.gray); Text(log.memo) } }; if let photoData = log.photoData, let uiImage = UIImage(data: photoData) { Image(uiImage: uiImage).resizable().scaledToFill().frame(height: 200).frame(maxWidth: .infinity).clipped().cornerRadius(10) } }.padding().background(Color.white).cornerRadius(15)
                }.padding()
            }
        }.navigationTitle(dateTitle).navigationBarTitleDisplayMode(.inline).toolbar { ToolbarItem(placement: .navigationBarTrailing) { NavigationLink(destination: EditLogView(log: log, bgColor: bgColor, mainColor: mainColor)) { Text("編集").bold().foregroundColor(mainColor) } } }
    }
}

// ==========================================
// ➕ 過去の記録を追加する画面
// ==========================================
struct AddPastLogView: View {
    var selectedDate: Date; let bgColor: Color; let mainColor: Color; @Environment(\.modelContext) private var modelContext; @Environment(\.dismiss) private var dismiss; @Query private var logs: [JibunLog]; @StateObject private var healthKitManager = HealthKitManager()
    
    @State private var score: Double = 5.0; @State private var mainActivity: String = "-"; @State private var socialInteraction: String = "-"
    @State private var activityLevel: Double = 3.0; @State private var stepCount: String = ""
    @State private var breakfast: String = "-"; @State private var lunch: String = "-"; @State private var dinner: String = "-"; @State private var foodMeat: String = "-"; @State private var foodFish: String = "-"; @State private var foodVeg: String = "-"; @State private var alcohol: String = "-"; @State private var caffeine: String = "-"; @State private var tobacco: String = "-"; @State private var exercise: String = "-"; @State private var sleepTime: String = "-"; @State private var sleepQuality: String = "-"; @State private var goodThing: String = ""; @State private var memo: String = ""; @State private var weather: String = "-"; @State private var photoData: Data? = nil; @State private var selectedPhotoItem: PhotosPickerItem? = nil

    let mainActivityOptions = ["-", "仕事", "遊び", "無"]; let levelOptions = ["-", "無", "少", "普", "多"]; let mealOptions = ["-", "無", "自炊", "購入", "外食"]; let sleepTimeOptions = ["-", "-3h", "4-6h", "6-8h", "8h-"]; let qualityOptions = ["-", "悪い", "普通", "良い"]

    @ViewBuilder func rowLabel(icon: String, text: String, color: Color = .primary) -> some View { HStack(spacing: 8) { Image(systemName: icon).foregroundColor(color).frame(width: 24); Text(text); Spacer() }.frame(width: 80) }

    var body: some View {
        ZStack {
            bgColor.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 30) {
                    VStack { HStack { Text("記録の点数").font(.headline).foregroundColor(.white); Spacer(); Text("\(Int(score)) 点").font(.title2).bold().foregroundColor(.white) }; Slider(value: $score, in: 0...10, step: 1).tint(.white) }.padding().background(mainColor).cornerRadius(15).shadow(color: mainColor.opacity(0.4), radius: 8, x: 0, y: 4)
                    
                    VStack(alignment: .leading, spacing: 15) { Picker("主にしたこと", selection: $mainActivity) { ForEach(mainActivityOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented); if mainActivity == "仕事" || mainActivity == "遊び" { Slider(value: $activityLevel, in: 1...5, step: 1).tint(mainColor) }; HStack { rowLabel(icon: "person.2.fill", text: "人との関わり", color: .purple); Spacer(); Picker("", selection: $socialInteraction) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; Divider(); HStack { rowLabel(icon: "cloud.fill", text: "天気", color: .cyan); Spacer(); Picker("", selection: $weather) { Text("-").tag("-"); Image(systemName: "sun.max.fill").tag("☀️"); Image(systemName: "cloud.fill").tag("☁️"); Image(systemName: "cloud.rain.fill").tag("☔️"); Image(systemName: "snowflake").tag("☃️") }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "shoeprints.fill", text: "歩数"); Spacer(); TextField("歩数", text: $stepCount).keyboardType(.numberPad).textFieldStyle(RoundedBorderTextFieldStyle()).frame(width: 100); Text("歩") } }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "fork.knife"); Text("食事").font(.headline) }; HStack { rowLabel(icon: "sun.max.fill", text: "朝", color: .orange); Spacer(); Picker("", selection: $breakfast) { ForEach(mealOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "sun.haze.fill", text: "昼", color: .orange); Spacer(); Picker("", selection: $lunch) { ForEach(mealOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "moon.stars.fill", text: "夜", color: .blue); Spacer(); Picker("", selection: $dinner) { ForEach(mealOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; Divider(); HStack { rowLabel(icon: "frying.pan.fill", text: "肉"); Spacer(); Picker("", selection: $foodMeat) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "fish.fill", text: "魚", color: .blue); Spacer(); Picker("", selection: $foodFish) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "carrot.fill", text: "野菜", color: .orange); Spacer(); Picker("", selection: $foodVeg) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) } }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "figure.run"); Text("習慣").font(.headline) }; HStack { rowLabel(icon: "wineglass.fill", text: "酒", color: .purple); Spacer(); Picker("", selection: $alcohol) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "mug.fill", text: "カフェイン", color: .brown); Spacer(); Picker("", selection: $caffeine) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "smoke.fill", text: "タバコ", color: .gray); Spacer(); Picker("", selection: $tobacco) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "dumbbell.fill", text: "運動", color: .green); Spacer(); Picker("", selection: $exercise) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) } }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "bed.double.fill"); Text("睡眠").font(.headline) }; HStack { rowLabel(icon: "clock.fill", text: "時間"); Spacer(); Picker("", selection: $sleepTime) { ForEach(sleepTimeOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "star.fill", text: "質", color: .yellow); Spacer(); Picker("", selection: $sleepQuality) { ForEach(qualityOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) } }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "square.and.pencil"); Text("振り返り").font(.headline) }; TextField("今日良かったこと（30文字以内）", text: $goodThing).textFieldStyle(RoundedBorderTextFieldStyle()).onChange(of: goodThing) { _, newValue in if newValue.count > 30 { goodThing = String(newValue.prefix(30)) } }; TextEditor(text: $memo).frame(height: 100).overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.gray.opacity(0.3), lineWidth: 1)); PhotosPicker(selection: $selectedPhotoItem, matching: .images) { HStack { Image(systemName: "photo.on.rectangle.angled"); Text(photoData == nil ? "今日の1枚を追加" : "写真を変更する") }.foregroundColor(mainColor).frame(maxWidth: .infinity).padding().background(mainColor.opacity(0.1)).cornerRadius(10) }.onChange(of: selectedPhotoItem) { _, newValue in Task { if let data = try? await newValue?.loadTransferable(type: Data.self) { photoData = data } } }; if let photoData, let uiImage = UIImage(data: photoData) { Image(uiImage: uiImage).resizable().scaledToFill().frame(height: 200).frame(maxWidth: .infinity).clipped().cornerRadius(10) } }.padding().background(Color.white).cornerRadius(15)
                    
                    Button(action: {
                        if let existingLog = logs.first(where: { Calendar.current.isDate($0.date, inSameDayAs: selectedDate) }) { modelContext.delete(existingLog) }
                        let newLog = JibunLog(date: selectedDate, score: score, mainActivity: mainActivity, activityLevel: Int(activityLevel), stepCount: stepCount, breakfast: breakfast, lunch: lunch, dinner: dinner, foodMeat: foodMeat, foodFish: foodFish, foodVeg: foodVeg, alcohol: alcohol, caffeine: caffeine, tobacco: tobacco, exercise: exercise, sleepTime: sleepTime, sleepQuality: sleepQuality, goodThing: goodThing, memo: memo, photoData: photoData, weather: weather, socialInteraction: socialInteraction)
                        modelContext.insert(newLog)
                        
                        AppNotificationManager.shared.updateNotifications(logs: logs)
                        dismiss()
                    }) { Text("過去の記録を保存する").font(.headline).foregroundColor(.white).frame(maxWidth: .infinity).padding(.vertical, 16).background(mainColor).cornerRadius(15) }.padding(.bottom, 40)
                }.padding(.horizontal, 20).padding(.top, 20)
            }
        }.navigationTitle({ let f = DateFormatter(); f.dateFormat = "M/d(E)の記録を追加"; f.locale = Locale(identifier: "ja_JP"); return f.string(from: selectedDate) }()).onAppear { healthKitManager.fetchSteps(for: selectedDate) { fetchedSteps in self.stepCount = fetchedSteps } }
    }
}

// ==========================================
// ✏️ 編集画面
// ==========================================
struct EditLogView: View {
    @Bindable var log: JibunLog; let bgColor: Color; let mainColor: Color; @State private var selectedPhotoItem: PhotosPickerItem? = nil; @Query private var logs: [JibunLog]
    let mainActivityOptions = ["-", "仕事", "遊び", "無"]; let levelOptions = ["-", "無", "少", "普", "多"]; let mealOptions = ["-", "無", "自炊", "購入", "外食"]; let sleepTimeOptions = ["-", "-3h", "4-6h", "6-8h", "8h-"]; let qualityOptions = ["-", "悪い", "普通", "良い"]
    @ViewBuilder func rowLabel(icon: String, text: String, color: Color = .primary) -> some View { HStack(spacing: 8) { Image(systemName: icon).foregroundColor(color).frame(width: 24); Text(text); Spacer() }.frame(width: 80) }

    var body: some View {
        ZStack {
            bgColor.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 30) {
                    VStack(alignment: .leading) { Text("日付の変更").font(.headline); DatePicker("日付", selection: $log.date, displayedComponents: .date).labelsHidden().environment(\.locale, Locale(identifier: "ja_JP")) }.padding().background(Color.white).cornerRadius(15)
                    VStack { HStack { Text("今日の点数").font(.headline).foregroundColor(.white); Spacer(); Text("\(Int(log.score)) 点").font(.title2).bold().foregroundColor(.white) }; Slider(value: $log.score, in: 0...10, step: 1).tint(.white) }.padding().background(mainColor).cornerRadius(15).shadow(color: mainColor.opacity(0.4), radius: 8, x: 0, y: 4)
                    
                    VStack(alignment: .leading, spacing: 15) { Picker("主にしたこと", selection: $log.mainActivity) { ForEach(mainActivityOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented); if log.mainActivity == "仕事" || log.mainActivity == "遊び" { Text("充実度・忙しさ: \(log.activityLevel)").font(.subheadline); Slider(value: Binding(get: { Double(log.activityLevel) }, set: { log.activityLevel = Int($0) }), in: 1...5, step: 1).tint(mainColor) }; HStack { rowLabel(icon: "person.2.fill", text: "人との関わり", color: .purple); Spacer(); Picker("", selection: $log.socialInteraction) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; Divider(); HStack { rowLabel(icon: "cloud.fill", text: "天気", color: .cyan); Spacer(); Picker("", selection: $log.weather) { Text("-").tag("-"); Image(systemName: "sun.max.fill").tag("☀️"); Image(systemName: "cloud.fill").tag("☁️"); Image(systemName: "cloud.rain.fill").tag("☔️"); Image(systemName: "snowflake").tag("☃️") }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "shoeprints.fill", text: "歩数"); Spacer(); TextField("歩数", text: $log.stepCount).keyboardType(.numberPad).textFieldStyle(RoundedBorderTextFieldStyle()).frame(width: 100); Text("歩") } }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "fork.knife"); Text("食事").font(.headline) }; HStack { rowLabel(icon: "sun.max.fill", text: "朝", color: .orange); Spacer(); Picker("", selection: $log.breakfast) { ForEach(mealOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "sun.haze.fill", text: "昼", color: .orange); Spacer(); Picker("", selection: $log.lunch) { ForEach(mealOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "moon.stars.fill", text: "夜", color: .blue); Spacer(); Picker("", selection: $log.dinner) { ForEach(mealOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; Divider(); HStack { rowLabel(icon: "frying.pan.fill", text: "肉"); Spacer(); Picker("", selection: $log.foodMeat) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "fish.fill", text: "魚", color: .blue); Spacer(); Picker("", selection: $log.foodFish) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "carrot.fill", text: "野菜", color: .orange); Spacer(); Picker("", selection: $log.foodVeg) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) } }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "figure.run"); Text("習慣・アクティビティ").font(.headline) }; HStack { rowLabel(icon: "wineglass.fill", text: "酒", color: .purple); Spacer(); Picker("", selection: $log.alcohol) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "mug.fill", text: "カフェイン", color: .brown); Spacer(); Picker("", selection: $log.caffeine) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "smoke.fill", text: "タバコ", color: .gray); Spacer(); Picker("", selection: $log.tobacco) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "dumbbell.fill", text: "運動", color: .green); Spacer(); Picker("", selection: $log.exercise) { ForEach(levelOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) } }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "bed.double.fill"); Text("睡眠").font(.headline) }; HStack { rowLabel(icon: "clock.fill", text: "時間"); Spacer(); Picker("", selection: $log.sleepTime) { ForEach(sleepTimeOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) }; HStack { rowLabel(icon: "star.fill", text: "質", color: .yellow); Spacer(); Picker("", selection: $log.sleepQuality) { ForEach(qualityOptions, id: \.self) { Text($0) } }.pickerStyle(.segmented) } }.padding().background(Color.white).cornerRadius(15)
                    VStack(alignment: .leading, spacing: 15) { HStack { Image(systemName: "square.and.pencil"); Text("振り返り").font(.headline) }; TextField("今日良かったこと（30文字以内）", text: $log.goodThing).textFieldStyle(RoundedBorderTextFieldStyle()).onChange(of: log.goodThing) { _, newValue in if newValue.count > 30 { log.goodThing = String(newValue.prefix(30)) } }; TextEditor(text: $log.memo).frame(height: 100).overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.gray.opacity(0.3), lineWidth: 1)); PhotosPicker(selection: $selectedPhotoItem, matching: .images) { HStack { Image(systemName: "photo.on.rectangle.angled"); Text(log.photoData == nil ? "写真を追加" : "写真を変更する") }.foregroundColor(mainColor).frame(maxWidth: .infinity).padding().background(mainColor.opacity(0.1)).cornerRadius(10) }.onChange(of: selectedPhotoItem) { _, newValue in Task { if let data = try? await newValue?.loadTransferable(type: Data.self) { log.photoData = data } } }; if let photoData = log.photoData, let uiImage = UIImage(data: photoData) { Image(uiImage: uiImage).resizable().scaledToFill().frame(height: 200).frame(maxWidth: .infinity).clipped().cornerRadius(10) } }.padding().background(Color.white).cornerRadius(15)
                }.padding(.horizontal, 20).padding(.vertical, 20)
            }
        }.navigationTitle("記録の編集")
        .onDisappear { AppNotificationManager.shared.updateNotifications(logs: logs) }
    }
}

// ==========================================
// ⚙️ 設定画面
// ==========================================
struct SettingsView: View {
    let bgColor: Color; let mainColor: Color
    @Environment(\.modelContext) private var modelContext; @Query private var logs: [JibunLog]
    
    @AppStorage("themeColorIndex") private var themeColorIndex = 0; @AppStorage("nickname") private var nickname = ""; @AppStorage("birthday") private var birthday = Date(); @AppStorage("postalCode") private var postalCode = ""; @AppStorage("isNotificationEnabled") private var isNotificationEnabled = true; @AppStorage("notificationHour") private var notificationHour = 23; @AppStorage("notificationMinute") private var notificationMinute = 0; @State private var notificationTime: Date = Date()
    @State private var showFileImporter = false; @State private var importMessage = ""; @State private var showImportAlert = false
    
    var body: some View {
        ZStack {
            bgColor.ignoresSafeArea()
            Form {
                Section(header: Text("プロフィール").font(.headline)) { TextField("ニックネーム", text: $nickname); DatePicker("誕生日", selection: $birthday, displayedComponents: .date).environment(\.locale, Locale(identifier: "ja_JP")); TextField("郵便番号", text: $postalCode).keyboardType(.numberPad) }
                Section(header: Text("アプリのデザイン").font(.headline)) { Picker("テーマカラー", selection: $themeColorIndex) { ForEach(ThemeColor.allCases) { theme in HStack { Circle().fill(theme.color).frame(width: 20, height: 20); Text(theme.name) }.tag(theme.rawValue) } }.pickerStyle(.navigationLink) }
                Section(header: Text("リマインド通知").font(.headline)) {
                    Toggle("毎日記録をお知らせ", isOn: $isNotificationEnabled).tint(mainColor)
                        .onChange(of: isNotificationEnabled) { _, _ in AppNotificationManager.shared.updateNotifications(logs: logs) }
                    if isNotificationEnabled {
                        DatePicker("通知の時間", selection: $notificationTime, displayedComponents: .hourAndMinute).environment(\.locale, Locale(identifier: "ja_JP"))
                            .onChange(of: notificationTime) { _, newValue in
                                let components = Calendar.current.dateComponents([.hour, .minute], from: newValue)
                                notificationHour = components.hour ?? 23; notificationMinute = components.minute ?? 0
                                AppNotificationManager.shared.updateNotifications(logs: logs)
                            }
                    }
                }
                Section(header: Text("データ管理").font(.headline), footer: Text("Excel等で編集したCSVを一括で取り込むことができます。")) { if let csvURL = generateCSV() { ShareLink(item: csvURL, message: Text("自分ログのデータです")) { HStack { Image(systemName: "square.and.arrow.up"); Text("CSVデータを書き出す") }.foregroundColor(mainColor) } }; Button(action: { showFileImporter = true }) { HStack { Image(systemName: "square.and.arrow.down"); Text("CSVデータを取り込む") }.foregroundColor(mainColor) } }
                Section(header: Text("アプリについて"), footer: Text("Jibunlog v1.0")) { HStack { Text("開発者"); Spacer(); Text("K").foregroundColor(.gray) } }
            }.scrollContentBackground(.hidden)
        }
        .navigationTitle("設定").navigationBarTitleDisplayMode(.inline)
        .onAppear { notificationTime = Calendar.current.date(bySettingHour: notificationHour, minute: notificationMinute, second: 0, of: Date()) ?? Date() }
        .fileImporter(isPresented: $showFileImporter, allowedContentTypes: [.commaSeparatedText]) { result in switch result { case .success(let file): importCSV(from: file); case .failure(let error): importMessage = "ファイルの選択に失敗しました: \(error.localizedDescription)"; showImportAlert = true } }
        .alert(isPresented: $showImportAlert) { Alert(title: Text("結果"), message: Text(importMessage), dismissButton: .default(Text("OK"))) }
    }
    
    private func generateCSV() -> URL? {
        var csvString = "日付,点数,天気,行動,充実度,歩数,朝食,昼食,夕食,肉,魚,野菜,酒,カフェイン,タバコ,運動,睡眠時間,睡眠の質,よかったこと,メモ,人との関わり\n"
        let formatter = DateFormatter(); formatter.dateFormat = "yyyy/MM/dd"
        for log in logs {
            let dateStr = formatter.string(from: log.date)
            let safeGoodThing = log.goodThing.replacingOccurrences(of: ",", with: "，").replacingOccurrences(of: "\n", with: " ")
            let safeMemo = log.memo.replacingOccurrences(of: ",", with: "，").replacingOccurrences(of: "\n", with: " ")
            let row = "\(dateStr),\(log.score),\(log.weather),\(log.mainActivity),\(log.activityLevel),\(log.stepCount),\(log.breakfast),\(log.lunch),\(log.dinner),\(log.foodMeat),\(log.foodFish),\(log.foodVeg),\(log.alcohol),\(log.caffeine),\(log.tobacco),\(log.exercise),\(log.sleepTime),\(log.sleepQuality),\(safeGoodThing),\(safeMemo),\(log.socialInteraction)\n"
            csvString.append(row)
        }
        let fileName = "Jibunlog_Data.csv"
        let tempURL = FileManager.default.temporaryDirectory.appendingPathComponent(fileName)
        do { try csvString.write(to: tempURL, atomically: true, encoding: .utf8); return tempURL } catch { return nil }
    }
    
    private func importCSV(from url: URL) {
        guard url.startAccessingSecurityScopedResource() else { return }; defer { url.stopAccessingSecurityScopedResource() }
        do {
            let data = try String(contentsOf: url, encoding: .utf8); let rows = data.components(separatedBy: "\n")
            let formatter = DateFormatter(); formatter.dateFormat = "yyyy/MM/dd"; var successCount = 0
            for (index, row) in rows.enumerated() {
                if index == 0 { continue }
                if row.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
                let columns = row.components(separatedBy: ",")
                if columns.count >= 21 {
                    if let date = formatter.date(from: columns[0]), let score = Double(columns[1]) {
                        if let existingLog = logs.first(where: { Calendar.current.isDate($0.date, inSameDayAs: date) }) { modelContext.delete(existingLog) }
                        let newLog = JibunLog(date: date, score: score, mainActivity: columns[3], activityLevel: Int(columns[4]) ?? 3, stepCount: columns[5], breakfast: columns[6], lunch: columns[7], dinner: columns[8], foodMeat: columns[9], foodFish: columns[10], foodVeg: columns[11], alcohol: columns[12], caffeine: columns[13], tobacco: columns[14], exercise: columns[15], sleepTime: columns[16], sleepQuality: columns[17], goodThing: columns[18], memo: columns[19], photoData: nil, weather: columns[2], socialInteraction: columns[20])
                        modelContext.insert(newLog); successCount += 1
                    }
                }
            }
            importMessage = "\(successCount) 件のデータを無事に取り込みました！"; showImportAlert = true
            AppNotificationManager.shared.updateNotifications(logs: logs)
        } catch { importMessage = "ファイルの読み込みに失敗しました。"; showImportAlert = true }
    }
}

// ==========================================
// 🪄 カレンダー魔法陣
// ==========================================
struct CustomCalendarView: UIViewRepresentable {
    @Binding var selectedDate: Date; var logs: [JibunLog]; var mainColor: Color
    func makeUIView(context: Context) -> UICalendarView {
        let view = UICalendarView(); view.calendar = Calendar.current; view.locale = Locale(identifier: "ja_JP"); view.fontDesign = .rounded; view.tintColor = UIColor(mainColor)
        let selection = UICalendarSelectionSingleDate(delegate: context.coordinator)
        selection.selectedDate = Calendar.current.dateComponents([.year, .month, .day], from: selectedDate)
        view.selectionBehavior = selection; view.delegate = context.coordinator
        return view
    }
    func updateUIView(_ uiView: UICalendarView, context: Context) {
        uiView.tintColor = UIColor(mainColor)
        context.coordinator.parent = self
        let selectedComponents = Calendar.current.dateComponents([.year, .month, .day], from: selectedDate)
        uiView.reloadDecorations(forDateComponents: [selectedComponents], animated: true)
    }
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    
    class Coordinator: NSObject, UICalendarViewDelegate, UICalendarSelectionSingleDateDelegate {
        var parent: CustomCalendarView
        init(_ parent: CustomCalendarView) { self.parent = parent }
        func dateSelection(_ selection: UICalendarSelectionSingleDate, didSelectDate dateComponents: DateComponents?) { if let date = dateComponents?.date { parent.selectedDate = date } }
        
        func calendarView(_ calendarView: UICalendarView, decorationFor dateComponents: DateComponents) -> UICalendarView.Decoration? {
            guard let date = dateComponents.date else { return nil }
            if let log = parent.logs.first(where: { Calendar.current.isDate($0.date, inSameDayAs: date) }) {
                let scoreInt = max(0, min(10, Int(log.score)))
                return .image(UIImage(systemName: "\(scoreInt).circle.fill"), color: UIColor(self.parent.mainColor), size: .large)
            }
            return nil
        }
    }
}
